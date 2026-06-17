---
type: plan
status: active
contract_version: 2
tags: [character-bible, voice-corpus, config, devlog, podcast]
refs: []
---

# Character Bible 语音料换成开发日志 Implementation Plan

**Goal:** 让 Character Bible 的语音蒸馏吃主播的开发日志(norvyn.com),而不是已 stale + 被 AI 污染的 `subjective_dir`(观点心得),修掉"流水线照着一个不像主播的'主播'统一腔调"这个平淡根因。

**Architecture:** 新增**可选** config key `vault.voice_corpus_dir`,只有 bible-distill 站(`_bible_distill_step`)读它,unset 时回退 `subjective_dir`(老 config 零回归)。`subjective_dir` 原封不动(达芬奇选题 + self_past_candidates 仍读它)。开发日志通过一个独立的、不进 runner 的同步脚本从公开 API 拉到本地目录,bible 只读这个干净目录。隔离语义不变 —— 开发日志同样不含当期 episode/news/cards,只是换了一个隔离的料源;它是 **VOICE 参照(怎么说话)**,绝不进 CONTENT(说什么)。

**Tech Stack:** Python 3 stdlib(`urllib`/`json`/`pathlib`),PyYAML(已是依赖),现有 `lib.bible.gather_corpus` / `lib.config` / `lib.runner`。

**Design doc:** none(standalone change,非 dev-guide phase)
**Design analysis:** none
**Crystal file:** none
**Bug diagnosis:** not applicable
**Threat model:** included
**Pre-flight risks:**
- 种料顺序依赖:`voice_corpus_dir` 必须在写进真实 config **之前**已存在于磁盘 —— config 对该 key present 时 fail-closed(不存在即 raise),会 halt 整条流水线。Task 5 强制"先 sync 建目录、再改 config"。
- `subjective_dir` 是共享 surface:已审计其消费者 —— 达芬奇选题 + `self_past_candidates`(`prep/SKILL.md`、`orchestrator.py`)继续读它,本计划不碰;`gather_corpus` 仅 `_bible_distill_step` 一个 caller(`runner.py:1545`)。无双 token、无漏改 caller。

---

## Impact Map

**User path:** 每期播客的腔调/声音 —— bible 经 step 12(定稿 voice-unification)/ step 13(口播稿 verbal-tics)统一主播声音,换料直接影响成片"像不像主播"。
**Data path:** `norvyn.com/api/posts` → `tools/sync-voice-corpus.py` → `voice_corpus_dir/*.md` → `gather_corpus` → `bible-distiller` 隔离蒸馏 → `state/character-bible.md` → step 12/13。
**Shared surfaces:** `lib/config.py` `VaultConfig`(全体消费者共享);bible 隔离不变量(`CLAUDE.md` / `SKILL.md`)。
**Existing consumers:** config 被全流水线消费;`subjective_dir` 仍被达芬奇/prep 读(选题 + self_past);bible step 是唯一改料源处。
**Must remain unchanged:** 达芬奇从 `subjective_dir` 选题 + 取 self_past_candidates;bible 隔离(永不见 episode/card/news/material);bible step 的 fail-soft minimal-bible 兜底;`select_draft` / 评分等下游全部。
**Regression checks:** 现有 `test_config` 全绿(无 voice_corpus_dir → None,不破);现有 bible-step / runner 测试全绿;`grep` 确认 davinci/prep 仍指向 `subjective_dir`。

---

## Threat Model

1. **Attack surface** — 同步脚本从 `norvyn.com/api/posts` 取 JSON,用返回的 `slug` 当**文件名**写盘:`slug` 含 `../` 或路径分隔符 = 路径穿越。`content` 成为 voice 料:这是主播自己的已发布站点,且 bible-distiller 既有不变量已把 corpus 视为 **DATA 非指令**;`gather_corpus` 自带 symlink-escape / binary / 超大 guard。
2. **Failure modes** — (a) sync 网络失败 → **fail-soft**:不删已有文件、非零退出 + stderr 提示(绝不静默留空目录,否则 bible 蒸出空 → 走 minimal-bible 静默退化);(b) config `voice_corpus_dir` present 但目录不存在 → **fail-closed** raise(与 config.py「never silent default」一致,逼出种料顺序)。
3. **Resource lifecycle** — sync 每篇先写临时文件再 `os.replace` 原子改名(部分失败不留半截);HTTP 用 `urllib` context-manager 关闭;无子进程 / 长连接 / socket 泄漏。
4. **Input validation** — `slug` → 只取 `os.path.basename` 并拒绝含 `/` / `..` / 空的(否则 skip 该篇 + 记 stderr);`title` 仅写进 frontmatter 文本;`content` 仅作文本写盘,不执行、不 eval。

---

<!-- section: task-1-tests keywords: config, voice_corpus_dir, test_config -->
### Task 1-tests: config `voice_corpus_dir` 解析测试(先写,必须 FAIL)

**Maps to Impact Map:** Shared surfaces, Regression checks

**Files:**
- Modify: `lib/tests/test_config.py`

**Expected outcome:** 测试覆盖新可选 key 的四种情形;在 impl 前运行**失败**(`VaultConfig` 无 `voice_corpus_dir` 字段 → `TypeError`/`AttributeError`)。

**Non-goals:** 不测 bible step(Task 2);不动 required-key 既有测试。

**Touched surface:** `test_config.py`。

**Regression shield:** 新增用例,不改既有 required-key / root 用例。

**Task Contract:**
- Expected behavior: 配置里写了开发日志目录就被识别;没写就当没有(不报错);写了但目录不存在就明确报错而不是静默忽略。
- Automated verify: `python3 -m pytest lib/tests/test_config.py -q` 在 impl 前 FAIL(新用例 import/属性错误)。
- Real path verify: 不适用(纯单测)。
- Manual/device verify: none。

**Steps:**
1. 加 `test_voice_corpus_dir_resolved_when_present`:临时建一个存在的 dir,config 写 `voice_corpus_dir: <dir>` → `load_config().vault.voice_corpus_dir == <dir>`。
2. 加 `test_voice_corpus_dir_none_when_absent`:不写该 key → `.vault.voice_corpus_dir is None`。
3. 加 `test_voice_corpus_dir_missing_path_raises`:写一个不存在的路径 → `pytest.raises(ConfigError, match="voice_corpus_dir")`。
4. 加 `test_voice_corpus_dir_empty_string_raises`:写空串 → `ConfigError`。
5. 复用文件里现有的 `tmp_path` + 写 config 的夹具风格(参照既有 `root` / required-key 用例)。

**Verify:**
Run: `python3 -m pytest lib/tests/test_config.py -q`
Expected: 4 个新用例存在且 FAIL(impl 未做),其余既有用例仍 PASS。
<!-- /section -->

<!-- section: task-1-impl keywords: config, voice_corpus_dir, VaultConfig -->
### Task 1-impl: config 加可选 `vault.voice_corpus_dir`

**Depends on:** Task 1-tests

**Maps to Impact Map:** Shared surfaces, Must remain unchanged, Regression checks

**Files:**
- Modify: `lib/config.py`(`VaultConfig` dataclass + `_validate_vault_paths`)

**Expected outcome:** Task 1-tests 全 PASS;`voice_corpus_dir` 是可选 key(像 `root` 一样不进 `REQUIRED_VAULT_KEYS`),present 时 fail-closed 存在性检查,absent → `None`。

**Non-goals:** 不动 `REQUIRED_VAULT_KEYS`;不动 subjective_dir/news_dir/output_dir 逻辑;不动 `root`。

**Touched surface:** `lib/config.py`。

**Regression shield:** 新字段带默认值 `None` → 现有调用 `VaultConfig(...)`、现有 config 文件全部不破。

**Task Contract:**
- Expected behavior: 同 1-tests —— 写了识别、没写当无、写错路径报错。
- Automated verify: `python3 -m pytest lib/tests/test_config.py -q` 全 PASS。
- Real path verify: `python3 -m lib.config --validate ~/Code/Content/Podcasts/config.yaml` 仍 `ok`(此时真实 config 还没加该 key → 走 absent 分支)。
- Manual/device verify: none。

**Steps:**
1. `VaultConfig` 加字段(放在 `root` **之后**;两者都是带默认值的可选字段,必须排在所有非默认字段之后,否则 dataclass 报 non-default-after-default 编译错):`voice_corpus_dir: str | None = None`。
2. `_validate_vault_paths` 在 return 前,仿 `root` 的可选处理但**加存在性检查**:
   ```python
   voice_raw = vault_raw.get("voice_corpus_dir")
   voice_resolved: str | None = None
   if voice_raw is not None:
       if not isinstance(voice_raw, str) or not voice_raw.strip():
           raise ConfigError("vault.voice_corpus_dir must be a non-empty string when set")
       vpath = Path(os.path.expanduser(voice_raw))
       if not vpath.exists():
           raise ConfigError(
               f"vault.voice_corpus_dir does not exist: {vpath} "
               f"(run tools/sync-voice-corpus.py to seed it)"
           )
       if not vpath.is_dir():
           raise ConfigError(f"vault.voice_corpus_dir is not a directory: {vpath}")
       voice_resolved = str(vpath)
   ```
3. return 改为 `VaultConfig(root=root_resolved, voice_corpus_dir=voice_resolved, **resolved)`。
4. `__main__` smoke 段加一行 `print(f"vault.voice_corpus_dir = {c.vault.voice_corpus_dir}")`(给 Task 5 当"是否真生效"的二道防线)。

**Verify:**
Run: `python3 -m pytest lib/tests/test_config.py -q`
Expected: 全 PASS(含 Task 1-tests 4 个新用例)。
<!-- /section -->

<!-- section: task-2-tests keywords: runner, bible, corpus-dir, test_runner -->
### Task 2-tests: bible 料源解析测试(先写,必须 FAIL)

**Maps to Impact Map:** Data path, Existing consumers

**Files:**
- Modify: `lib/tests/test_runner.py`(或新建 `lib/tests/test_bible_corpus_source.py`,与现有 bible-step 测试同风格)

**Expected outcome:** 测一个待新增的纯函数 `lib.runner._bible_corpus_dir(config)`:有 `voice_corpus_dir` 时返回它,只有 `subjective_dir` 时返回后者;impl 前 FAIL(函数不存在)。

**Non-goals:** 不端到端跑 distiller;不测 gather_corpus 内部(已有测试)。

**Touched surface:** 测试文件。

**Regression shield:** 新用例,不改既有 runner 测试。

**Task Contract:**
- Expected behavior: 配了开发日志目录就用它做语音料;没配就退回旧的笔记目录。
- Automated verify: `python3 -m pytest lib/tests/ -k bible_corpus -q` impl 前 FAIL(`AttributeError: _bible_corpus_dir`)。
- Real path verify: 不适用(纯单测)。
- Manual/device verify: none。

**Steps:**
1. 用最小假 config(`types.SimpleNamespace(vault=SimpleNamespace(voice_corpus_dir="/voice", subjective_dir="/subj"))`)断言 `_bible_corpus_dir(cfg) == "/voice"`。
2. `voice_corpus_dir=None` → 返回 `"/subj"`。
3. `vault=None` / 两者皆 None → 返回 `None`(不崩)。

**Verify:**
Run: `python3 -m pytest lib/tests/ -k bible_corpus -q`
Expected: 用例存在且 FAIL。
<!-- /section -->

<!-- section: task-2-impl keywords: runner, bible, _bible_distill_step, voice_corpus_dir -->
### Task 2-impl: bible step 读 `voice_corpus_dir`,回退 `subjective_dir`

**Depends on:** Task 2-tests

**Maps to Impact Map:** Data path, Existing consumers, Must remain unchanged

**Files:**
- Modify: `lib/runner.py`(新增 `_bible_corpus_dir` helper;`_bible_distill_step` line 1536-1542 改用它;函数 docstring line 1515-1518)

**Expected outcome:** Task 2-tests 全 PASS;bible-distill 从 `voice_corpus_dir`(unset 回退 `subjective_dir`)gather 料;隔离 / fail-soft / minimal-bible 全不变。

**Non-goals:** 不改 gather_corpus、不改 distiller prompt、不改 fail-soft 兜底、不动 cap 常量(84KB 远低于 150KB)。

**Touched surface:** `lib/runner.py`。

**Regression shield:** 回退分支保证旧 config(无 voice_corpus_dir)行为逐字不变;隔离源仍是单一目录,不混入 episode/card。

**Task Contract:**
- Expected behavior: 配了开发日志后,这一步去读开发日志;没配则照旧读笔记目录,行为不变。
- Automated verify: `python3 -m pytest lib/tests/ -k bible -q` 全 PASS。
- Real path verify: Task 5 的真实 bible-distill run(人读产出 bible)。
- Manual/device verify: none。

**Steps:**
1. 加模块级 helper:
   ```python
   def _bible_corpus_dir(config) -> Optional[str]:
       vault = getattr(config, "vault", None)
       return (getattr(vault, "voice_corpus_dir", None)
               or getattr(vault, "subjective_dir", None))
   ```
2. `_bible_distill_step` 把 line 1536-1538 的 `subjective_dir = getattr(...)` 换成 `corpus_dir = _bible_corpus_dir(config)`,后续 `if subjective_dir:` / `gather_corpus(subjective_dir, ...)` 同步改名为 `corpus_dir`。
3. 更新 docstring(line 1515-1518):"Gathers the host's voice corpus (`vault.voice_corpus_dir`, 回退 `vault.subjective_dir`) ... 仍 ISOLATED(never episodes/news/cards/material)"。

**Verify:**
Run: `python3 -m pytest lib/tests/ -k bible -q`
Expected: 全 PASS;`grep -n "_bible_corpus_dir" lib/runner.py` 命中 helper + 调用点。
<!-- /section -->

<!-- section: task-3-tests keywords: sync-voice-corpus, fetch, filter, mock -->
### Task 3-tests: 同步脚本测试(先写,必须 FAIL)

**Maps to Impact Map:** Data path

**Files:**
- Create: `lib/tests/test_sync_voice_corpus.py`

**Expected outcome:** mock API 响应下,脚本只写开发日志、frontmatter 正确、路径穿越 slug 被拒;mock 网络失败时不清空已有目录且非零退出。impl 前 FAIL(模块不存在)。

**Non-goals:** 不打真网络(用 monkeypatch);不测 bible/gather。

**Touched surface:** 新测试文件。

**Regression shield:** 独立新文件。

**Task Contract:**
- Expected behavior: 同步脚本只挑开发日志、写成干净 md;网断了不会把已有的料删掉。
- Automated verify: `python3 -m pytest lib/tests/test_sync_voice_corpus.py -q` impl 前 FAIL(`ModuleNotFoundError`)。
- Real path verify: Task 3-impl 的真实 `--dry-run` / 真跑(见下)。
- Manual/device verify: none。

**Steps:**
1. 把脚本的 fetch 设计成可注入(`fetch_posts(url, *, opener=urllib.request.urlopen)` 之类),测试 monkeypatch 它返回固定 JSON(含 2 篇开发日志 + 1 篇非开发日志 + 1 篇恶意 slug `../evil`)。
2. 断言:输出目录只出现 2 个开发日志 `.md`;非开发日志被过滤;`../evil` 被拒(不写、记 stderr);frontmatter `--- title: ... ---` + 正文存在。
3. 断言网络失败路径:opener 抛异常 → 预置的已有文件**仍在**、进程返回非零。

**Verify:**
Run: `python3 -m pytest lib/tests/test_sync_voice_corpus.py -q`
Expected: 用例存在且 FAIL。
<!-- /section -->

<!-- section: task-3-impl keywords: sync-voice-corpus, urllib, devlog, tools -->
### Task 3-impl: `tools/sync-voice-corpus.py` —— 把开发日志同步到本地

**Depends on:** Task 3-tests

**Maps to Impact Map:** Data path

**Files:**
- Create: `tools/sync-voice-corpus.py`

**Expected outcome:** Task 3-tests 全 PASS;手动跑能把 norvyn.com 上的开发日志全量(当前 8 篇)拉成干净 `.md` 落进目标目录;**不进 runner**(日常流水线保持离线确定性)。

**Non-goals:** 不接 runner、不接 cron、不引第三方依赖;不抓非开发日志(本期 scope 只要 VOICE 系列)。

**Touched surface:** 新脚本。

**Regression shield:** 纯新增、独立运行;失败 fail-soft 不动既有料。

**Task Contract:**
- Expected behavior: 跑一下就把"开发日志"系列拉到本地一个文件夹,网断了不毁旧料。
- Automated verify: `python3 -m pytest lib/tests/test_sync_voice_corpus.py -q` 全 PASS。
- Real path verify: `python3 tools/sync-voice-corpus.py --out /tmp/voice-corpus-test` → `ls /tmp/voice-corpus-test/*.md` 出现开发日志 1-8 且内容干净。
- Manual/device verify: none。

**Steps:**
1. stdlib only:`argparse`(`--out` 默认从 `lib.config.load_config().vault.voice_corpus_dir`;`--source-url` 默认 `https://norvyn.com/api/posts?limit=200`;`--filter` 默认 `开发日志`)、`urllib.request`、`json`、`os`/`tempfile`/`pathlib`。
2. `fetch_posts(url, *, opener=urllib.request.urlopen)` 返回 `data` 列表(注入点供测试);默认 opener 必须 `urllib.request.urlopen(url, timeout=30)` —— **不带 timeout 时 norvyn.com 挂起会 hang 而非抛异常,`except Exception` 接不住,违背 Threat Model §2(a) fail-soft**。
3. 过滤:`p` 满足 `filter in p.get("title","")`(主信号)`or p.get("slug","").startswith("kai-fa-ri-zhi")`。
4. 每篇:`name = os.path.basename(p["slug"])`;拒绝 `not name or name in {".",".."} or "/" in p["slug"] or ".." in p["slug"]`(穿越防护,记 stderr,skip)。写 `--- \ntitle: {title}\n---\n\n{content}`(content 里 `\\n` 规范化为真实换行),temp + `os.replace` 原子落 `<out>/<name>.md`;写 temp 期间出错 try/except `unlink` 清理(仿 `lib/bible.write_bible`),不留 orphan temp。
5. fail-soft:fetch 抛异常 → `print(..., file=sys.stderr); sys.exit(2)`,**不**触碰已有文件(不预清目录)。
6. 成功打印写了几篇 + 目标目录。

**Verify:**
Run: `python3 tools/sync-voice-corpus.py --out /tmp/voice-corpus-test && ls -1 /tmp/voice-corpus-test/*.md | wc -l`
Expected: ≥8;抽读一篇含 `title:` frontmatter + 正文(如开发日志6 的"修bug和治病")。
<!-- /section -->

<!-- section: task-4-docs keywords: CLAUDE, SKILL, bible-docstring, invariant -->
### Task 4: 文档/不变量同步(VOICE 非 CONTENT、料源更新)

⚠️ No test: 纯文档(.md / docstring),无逻辑;由 grep 核对措辞。

**Maps to Impact Map:** Shared surfaces

**Files:**
- Modify: `CLAUDE.md`(bible 隔离不变量段:"fed ONLY the `gather_corpus(subjective_dir)`")
- Modify: `skills/podcast/SKILL.md`(step 6 正文 + 末尾 contract table 第 6 行)
- Modify: `lib/bible.py`(模块 docstring 顶部 "(`vault.subjective_dir`)")

**Expected outcome:** 三处都说明 bible 语音料 = `voice_corpus_dir`(回退 `subjective_dir`);明确**隔离语义不变**(仍不见 episode/card/news)+ 开发日志是 **VOICE 参照非 CONTENT 模板**。

**Non-goals:** 不改隔离不变量的**实质**(只更新料源描述);不动其他 step 的契约。

**Touched surface:** 上述三文件。

**Regression shield:** 仅措辞;`grep` 确认无残留"fed ONLY gather_corpus(subjective_dir)"旧表述被当成代码契约。

**Task Contract:**
- Expected behavior: 读文档的人知道 bible 现在学开发日志的"腔调",但不会拿它当选题来源。
- Automated verify: N/A — 文档编辑;`grep -rn "voice_corpus_dir" CLAUDE.md skills/podcast/SKILL.md lib/bible.py` 命中。
- Real path verify: 不适用。
- Manual/device verify: none。

**Steps:**
1. `CLAUDE.md` bible 隔离不变量:把料源从 `gather_corpus(subjective_dir)` 改述为 voice corpus(`voice_corpus_dir`,回退 `subjective_dir`),保留"必须隔离 / 永不见 episode/card/news/material / 是 VOICE+LENS 非 content template"全部原意,补一句"语音料现取自主播开发日志(VOICE 怎么说话),不是选题来源(CONTENT)"。
2. `SKILL.md` step 6 + contract table 第 6 行:同步料源描述(`voice_corpus_dir` 回退 `subjective_dir`)。
3. `lib/bible.py` 模块 docstring 顶部一句更新料源。

**Verify:**
Run: `grep -rn "voice_corpus_dir\|VOICE" CLAUDE.md skills/podcast/SKILL.md lib/bible.py | head`
Expected: 三文件均命中新表述,无遗留把 subjective_dir 当 bible 唯一料源的硬契约语句。
<!-- /section -->

<!-- section: task-5-golive keywords: config, seed, sync, real-path -->
### Task 5: 真实 config 接入 + 种料(go-live,顺序关键)

⚠️ No test: 真实 config 编辑 + 运维动作,无单测;real-path verify 是验收门。

**Maps to Impact Map:** User path, Data path

**Files:**
- Modify: `~/Code/Content/Podcasts/config.yaml`(加 `voice_corpus_dir`)
- (运行)`tools/sync-voice-corpus.py` 种 `~/.podcast-studio/voice-corpus/`

**Expected outcome:** 真实流水线的 bible 开始吃开发日志;`load_config` 不 fail-closed(目录已种)。

**Non-goals:** 不动 subjective_dir/news_dir/output_dir;不跑含 TTS 的整期(贵,TTS 不在兜底额度)。

**Touched surface:** 真实 config + 新目录。

**Regression shield:** 严格"先 sync 后改 config"顺序 —— 否则 config present-but-missing 直接 halt 流水线。

**Task Contract:**
- Expected behavior: 之后每次出片,bible 是从开发日志蒸出来的,腔调更像主播。
- Automated verify: N/A — 配置/运维;由下方 real-path verify 担保。
- Real path verify:(1)先 `python3 tools/sync-voice-corpus.py --out ~/.podcast-studio/voice-corpus`(建目录+灌料);(2)再给 config.yaml 的 `vault:` 加 `voice_corpus_dir: ~/.podcast-studio/voice-corpus`;(3)`python3 -m lib.config --validate ~/Code/Content/Podcasts/config.yaml` → `ok`,再 `python3 -m lib.config` 看打印的 `vault.voice_corpus_dir` 真指向种好的目录(二道"看着改了其实没生效"防线);(4)绕 clone gotcha,按绝对路径在 working repo 真实 `claude -p` 跑 bible-distill 一站,**人读** `state/character-bible.md`,对比换料前(stale 井蒸的)是否明显更像主播(自嘲 / 具体比喻 / "别人靠不住我自己来"姿态)。
- Manual/device verify: 人读 bible 的主观判断(这是本计划的核心验收,模型不能自判"够像")。

**Steps:**
1. **先**跑 sync 建目录 + 灌开发日志(顺序硬约束)。
2. **后**编辑真实 config 加 key。
3. validate config → ok。
4. 真实 bible-distill 一站 → 人读 bible。判据:明显更像他 = 成功;无变化/更糟 = 回查 gather 是否真读到新目录(警惕本仓库反复出现的"看着改了其实没生效")。

**Verify:**
Run: `python3 -m lib.config --validate ~/Code/Content/Podcasts/config.yaml`
Expected: `ok`(种料在前,目录存在)。随后人读 bible 做主观验收。
<!-- /section -->

<!-- section: task-6-park keywords: memory, sidecar, parking -->
### Task 6: 挂起 B/C(他者观察者 + 自改进 PR)并设提醒

⚠️ No test: memory 文件写入,无逻辑。

**Maps to Impact Map:**(无代码 surface;项目记忆)

**Files:**
- Create: `~/.claude/projects/-Users-norvyn-Code-Skills-personal-os/memory/podcast_sidecar_parked.md`
- Modify: 同目录 `MEMORY.md`(加一行索引)

**Expected outcome:** B/C(跨期他者批评者 + 自改进 PR,锚=开发日志)被记下,含"何时重提"的触发条件,下个 session 在上下文里能提醒用户。

**Non-goals:** 现在不实现 B/C 任何代码。

**Touched surface:** 项目记忆。

**Task Contract:**
- Expected behavior: 以后合适的时候(换料跑几期后)能被提醒"要不要做那个外人观察者"。
- Automated verify: N/A — 记忆写入;`test -f` 该文件。
- Real path verify: 不适用。
- Manual/device verify: none。

**Steps:**
1. 写 parked memory:B=跨期脱环批评者(拿开发日志当尺给 episode 挑刺、出诊断报告)、C=B+rubric 防火墙+可证伪判据+PR 提案人 merge;锚已定=norvyn.com 开发日志;**重提触发**=Task 5 换料后跑几期真实 episode、判断"有没有变暖"之后,据此评估 B/C 必要性。
2. `MEMORY.md` 加一行指针。

**Verify:**
Run: `test -f ~/.claude/projects/-Users-norvyn-Code-Skills-personal-os/memory/podcast_sidecar_parked.md && echo ok`
Expected: `ok`。
<!-- /section -->

---

## Decisions
None.

- `voice_corpus_dir` present 时 **fail-closed**(目录不存在即 raise),而非静默退化:由 `lib/config.py` 既有「never a silent default」契约决定(`_validate_vault_paths` 对 required dirs 全部 fail-closed),不是可选项。
- **替换**而非追加 `subjective_dir`:用户明确"把 bible 换成开发日志"。
- 本期过滤**只取开发日志系列**(非全部博文):用户原话指向"开发日志"。后续可拓宽到其他"真他写"的文章 —— 记为 Recommended addition(not in scope),不建任务。
- **sync 手动触发**:`tools/sync-voice-corpus.py` 不进 runner —— 新写的开发日志不会自动进 voice corpus;想让 bible 用上最新日志,每次手动跑一次 sync(刻意保持日常流水线离线确定)。

---
## Verification
- **Verdict:** Approved
- **Date:** 2026-06-17
- **Cycle:** must-revise → 5/5 items applied → re-verified approved (reports `.claude/reviews/plan-verifier-2026-06-17-114226.md` + `-115000.md`)
