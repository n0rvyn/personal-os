---
name: commit-stats
description: "Use when the user says 'commit stats', '提交统计', 'git commit trend', '项目提交趋势', or wants to analyze git commit history across projects. Generates an HTML report showing daily commit trends, project distribution, and summary statistics."
user_invocable: true
model: sonnet
allowed-tools:
  - Bash
  - Write
---

## Overview

Analyze git commit history across all projects in a directory, generate an interactive HTML report with daily commit trends, project-wise distribution charts, and summary statistics. Uses Chart.js for visualization.

## Arguments

Parse from user input:
- `--dir PATH`: Base directory to scan for git repos (default: current working directory)
- `--since DATE`: Start date in YYYY-MM-DD format (default: 9 months ago)
- `--until DATE`: End date in YYYY-MM-DD format (default: today)
- `--output FILE`: Output HTML file path (default: {base_dir}/commit-stats.html)

## Steps

### Step 1: Discover Git Repos

Scan the base directory for all subdirectories containing a `.git` folder:

```bash
for item in "$BASE_DIR"/*; do
  if [ -d "$item/.git" ]; then
    echo "$item"
  fi
done
```

### Step 2: Collect Commit Data

For each discovered repo, collect commit dates:

```bash
git -C "$REPO_PATH" log --since="$SINCE_DATE" --until="$UNTIL_DATE" --format="%ai" --date=short
```

Aggregate commits per day per project.

### Step 3: Generate HTML Report

Create a complete HTML file with:
- Summary cards (total commits, active projects, daily average, peak day)
- Line chart for daily total commit trend
- Bar chart for project distribution (top 15)
- Multi-line chart for all projects trend (toggleable Top10/All, searchable)
- Table with project stats (commits, daily average, peak, status badge)

Use embedded Chart.js from CDN. Color-code projects consistently across charts.

### Step 4: Open Report

Open the generated HTML file in the default browser:

```bash
open "$OUTPUT_FILE"
```

## Data Structures

```python
# Collected data shape
{
  "projects": ["proj1", "proj2", ...],
  "project_data": {
    "proj1": {"daily": {"2025-08-01": 3, "2025-08-02": 5}, "total": 150},
    ...
  },
  "dates": ["2025-08-01", "2025-08-02", ...]
}

# Project status badges
- 高频 (high): >500 commits
- 中频 (mid): 100-500 commits
- 低频 (low): 1-99 commits
- 无提交 (none): 0 commits
```

## HTML Template Structure

```
- Summary cards row (6 cards)
- Daily trend line chart (full width)
- Project distribution bar chart (top 15, horizontal)
- All projects multi-line chart (with search + toggle)
- Project table (sortable, searchable)
```

## Completion Criteria

- HTML file generated at specified path
- All charts render with real data
- Report opens automatically in browser
- Summary statistics are accurate (totals, averages, peaks)

## Example Output

```
✅ Commit stats report generated:
- Report: /path/to/commit-stats.html
- 35 repos scanned, 3322 total commits
- Peak day: 2026-03-15 (87 commits)
```
