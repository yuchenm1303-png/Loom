# 在自己的 Loom 里用官方 MiniMax 测调查员

前提：已经装上带 `investigate` 的版本（关于里能看到对应版本号；Claude 会用文件版本号核对）。

1. 在 Loom 里开一个新对话（对话模式，不是建造），工作区选 `C:\tmp\inv-minimax\workspace`（一份代码快照，不是你的项目），模型用内置 MiniMax，权限选完全访问。
2. 发第一条，等它答完：

   > 请完整读一遍 app/agent_runtime/runtime.py 和 app/project_runner/service.py 这两个文件（全文，不要只搜索），然后用五句话告诉我它们各自负责什么。

3. 再发第二条，等它答完：

   > 这个仓库里所有启动子进程的地方（subprocess.run、Popen、check_output、asyncio.create_subprocess_exec 等）都在哪些文件、哪些函数里？每一处有没有设置 stdin？把没设置 stdin 的列成清单给我，我先看看，不用改代码。

4. 告诉 Claude 做完了。它只读 `~/.loom` 里这个对话的事件记录：有没有调用 investigate、被挡一次后怎么反应、各花了多少 token、答案对不对。

同样的两条在 deepseek-v4.1-flash 上的结果见 `docs/sub-agents-2026-11-08.md`。
