# 协议宪法

> 三款产品（BrainMem / BrainHub / BrainBridge）共同遵守的协议定稿。
> **唯一真源在 BrainMem 仓库**：[`../brainmem/docs/protocol-constitution.md`](../brainmem/docs/protocol-constitution.md)
>
> 本文件只是指针，避免三份副本不同步。写代码前必读上面那份。

协议宪法包含三部分：

1. **MCP 工具签名**（12 个工具的输入/输出/实现方）— BrainHub 实现 write_note/read_file/list_files/projects/health_check + **send_matrix_msg**（经命名管道转发给 BrainBridge daemon），转发 BrainMem 的 search/query/reindex。任何 agent 只连 BrainHub MCP，单一入口。
2. **memory.db schema**（sqlite-vec + FTS5 + 图谱同库）— BrainHub 与 BrainMem 共享同一份 memory.db，追加自己的 files/projects/tasks/ops_log 表（见 [PLAN.md](../PLAN.md)）。
3. **Matrix 消息 envelope + 命名管道**（BrainHub ↔ BrainBridge daemon 传输）— BrainHub 暴露 send_matrix_msg MCP 工具，经 `\\.\pipe\brain-matrix-out` 写 envelope 给 BrainBridge daemon 真正收发；BrainBridge 经 `brain-matrix-in` + `brain-agent-status` 推回 BrainHub 转 WS。详见真源 §3.2。

**原则**：协议定死后不再动。产品各自迭代但遵守同一套，这是产品族不退化成拼装拼凑的唯一保障。
