# 存储约定:cwd 模型(crystal D-010)

novel-forge **不维护中央书库注册表**。和 dev-workflow 认代码仓库一样,novel-forge 只认**当前工作目录(cwd)**。

## 核心约定

**一本书 = 一个目录。这个目录就是工作目录。** 作者 cd 进书目录跑 skill,所有产出落本地。

```
<书目录>/                  ← cwd,作者自己决定放哪、叫什么
├── bible.md              ← 故事圣经(可见,作者可直接编辑)
├── outline.md            ← 分卷章纲(可见)
├── chapters/
│   ├── ch-001.md         ← 定稿章节(可见)
│   ├── ch-002.md
│   └── ...
└── .novel/
    └── state.json        ← 机械状态(隐藏,不碍浏览正文)
```

## 规则

1. **plugin 只读写 cwd 内文件**。绝不写进 plugin 安装目录,绝不维护 `~/novels/` 之类的中央根。
2. **可见 / 隐藏分层**:正文相关(`bible.md` / `outline.md` / `chapters/`)可见,作者随时手改;机械状态(`.novel/state.json`)隐藏。
3. **天然红利**:书目录可 `git init`,章节即 commit,版本史/回滚免费;移动、备份、多端同步全由作者掌控。
4. **多本书** = 多个目录,互不干扰,无需注册。

## 被否方案(D-010)

- ❌ `novel_root` 中央配置(在 `~/.claude/personal-os.yaml` 加 novel 根目录,plugin 管理子目录):否决——多余间接层,与 dev-workflow 的 cwd-based 不一致,且强加目录结构、削弱作者掌控。
