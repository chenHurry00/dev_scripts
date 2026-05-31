# GPU共享访问与容器资源监控实施方案

> 本文档仅定义实施方案与接口边界，当前阶段不修改代码。

## 目标

在现有 `dockerhub-manager` 基础上补齐两类能力：

1. 在容器管理页和资源卡片中显示容器实时资源占用：
   - CPU `%`
   - 内存 `已用 / 限制`
   - 磁盘 `可写层占用`
   - GPU 使用情况
2. 支持创建可访问 GPU 的 Docker 容器，同时允许多个容器共享同一张 GPU。
3. 为容器增加“可写层磁盘限额”，且不限制挂载目录与数据盘。

## 核心约束

### 1. GPU 不能独占

平台不对 GPU 做独占锁。

同一张 GPU 可以授权给多个容器使用。平台只负责：

- 控制容器能访问哪些 GPU；
- 展示每个容器当前对 GPU 的使用情况；
- 在 UI 中提示 GPU 已被哪些容器共享。

### 2. GPU 配置不走 `docker update`

现有 CPU、内存、PIDs 可以走：

```text
PATCH /containers/<name>/resources
```

但 GPU 访问权限属于容器启动参数，不能按现有模式原地更新。

结论：

- `CPU / 内存 / PIDs`：继续支持在线修改；
- `GPU 可访问设备`：创建时指定；
- 已创建容器如需修改 GPU 配置，走“重建容器并保留挂载数据”流程。

### 3. 可写层磁盘限额不走 `docker update`

当前方案中的磁盘限额指：

- 容器自身可写层大小上限；
- 不包含 bind mount；
- 不包含 Docker volume；
- 不包含宿主机挂载数据盘。

该能力使用容器创建参数实现，而不是运行时更新。

结论：

- 可写层磁盘限额在创建容器时指定；
- 后续若要修改，走“重建容器并保留挂载数据”；
- 不能按 `CPU / 内存 / PIDs` 的方式原地在线更新。

### 4. 实时资源不写入 `data.json`

实时指标不能持久化到中心面板配置文件。

原因：

- 更新频率高；
- 会造成无意义的频繁写盘；
- 会放大锁竞争；
- 容易把“静态配置”和“实时状态”混在一起。

因此：

- `data.json` 只保存静态配置；
- 实时状态由 Agent 现查现返回；
- 面板内存中可做短时缓存，默认 5 到 10 秒刷新。

## 当前现状

当前系统已有：

- Agent 容器列表接口：`GET /containers`
- Agent 资源更新接口：`PATCH /containers/<name>/resources`
- 面板容器列表接口：`GET /api/containers`

但当前返回内容基本都是静态信息：

- 容器名称
- 镜像
- 状态
- 端口
- 资源限制

尚未包含：

- `docker stats` 级别的 CPU / 内存运行时占用；
- `docker inspect --size` 的容器可写层占用；
- 容器可写层磁盘限额；
- NVIDIA 驱动与 `nvidia-container-toolkit` 能力检测；
- GPU 列表、GPU 可见性配置、GPU 进程归因。

## 设计原则

### 1. 共享 GPU 优先

第一版不做 GPU 独占，不做 GPU 配额调度器，不做排他锁。

容器创建时只指定：

- 是否启用 GPU；
- 可访问哪些 GPU；
- 访问模式固定为共享。

交互默认值：

- 一旦启用 GPU，默认分配该服务器的全部 GPU；
- 前端默认全选全部 GPU；
- 若后续需要只给部分 GPU，再手动取消部分选择。

### 2. 指标口径要明确

容器页展示的资源占用定义如下：

| 指标 | 口径 |
| --- | --- |
| CPU `%` | 容器当前 CPU 使用率 |
| 内存 `GB` | 容器当前已用内存 / 容器内存上限 |
| 磁盘 `GB` | 容器可写层占用，仅 `SizeRw`，不含挂载目录与 volume 数据 |
| GPU 显存 | 容器内 GPU 进程显存占用总和 |
| GPU 利用率 `%` | 容器内 GPU 进程采样利用率总和；若驱动不支持，则降级展示为“仅显存可见” |

其中磁盘指标必须在 UI 上明确写成：

```text
磁盘占用（可写层）
```

避免用户误解为包含挂载数据盘。

### 3. GPU 利用率按进程归因

GPU 利用率不从容器内自报，不从整卡粗暴平摊，而是在宿主机侧归因：

1. 读取 GPU 正在运行的进程；
2. 将进程 PID 映射到 Docker 容器；
3. 按容器聚合 GPU 指标。

这比“整卡利用率均分给所有容器”更合理。

## 服务器前置条件

目标服务器若要支持 GPU，必须满足：

1. 已安装 NVIDIA 驱动；
2. `nvidia-smi` 可正常执行；
3. 已安装 `nvidia-container-toolkit`；
4. Docker 具备 `--gpus` 运行能力；
5. Agent 进程有权限读取 NVML 和 `/proc/<pid>/cgroup`。

Agent 启动后需要对这些条件做显式检查，并返回结构化能力信息。

默认策略：

- Agent 只做检查，不自动安装 GPU 驱动或 `nvidia-container-toolkit`；
- 不在普通“创建容器”流程里静默修改宿主机；
- 如果发现缺项，返回缺失项、安装建议和下一步动作建议。

## 数据模型调整

### 容器静态配置

中心面板保存以下新增字段：

```json
{
  "gpu_enabled": true,
  "gpu_driver": "nvidia",
  "gpu_devices": ["0", "1"],
  "gpu_mode": "shared",
  "rootfs_limit": "120g"
}
```

说明：

- `gpu_enabled`：是否启用 GPU；
- `gpu_driver`：当前固定为 `nvidia`；
- `gpu_devices`：容器允许访问的 GPU 编号或稳定标识；
- `gpu_mode`：当前固定为 `shared`；
- `rootfs_limit`：容器可写层上限，建议支持时默认预填 `120g`。

### 实时指标结构

实时指标不持久化，建议由 Agent 返回：

```json
{
  "cpu_percent": 12.4,
  "memory_used_bytes": 3657433088,
  "memory_limit_bytes": 8589934592,
  "disk_rw_bytes": 1289748480,
  "gpu": {
    "enabled": true,
    "utilization_supported": true,
    "devices": [
      {
        "id": "0",
        "uuid": "GPU-xxxx",
        "name": "NVIDIA RTX 4090",
        "container_memory_used_bytes": 5368709120,
        "container_util_percent": 42,
        "device_util_percent": 68,
        "device_memory_used_bytes": 17179869184,
        "device_memory_total_bytes": 25769803776
      }
    ]
  }
}
```

## Agent 侧实施方案

## 一、系统能力检测

Agent 新增 GPU 能力检测函数，纳入 `/checks` 或单独接口返回：

- 驱动是否安装；
- `nvidia-smi` 是否可执行；
- `nvidia-container-toolkit` 是否可用；
- Docker 是否支持 `--gpus`；
- 当前服务器 GPU 列表；
- 是否支持进程级 GPU 利用率采样。

建议新增接口：

```text
GET /gpu/info
```

返回：

- 服务器是否支持 GPU；
- 每张 GPU 的 `index / uuid / name / memory_total`；
- 当前整卡 `util / memory_used`；
- 驱动与 toolkit 状态。
- Docker 存储驱动与可写层限额支持状态。
- 若不满足 GPU 条件，返回结构化安装建议与缺失项列表。

交互原则：

- 检查到 GPU 能力缺失时，默认不自动安装；
- 前端弹出建议卡片，列出：
  - 缺失项；
  - 安装前提；
  - 可能影响，例如需要 `sudo`、可能需要重启 Docker；
- 然后让管理员二选一：
  - `继续安装`
  - `退出`

如果后续实现“继续安装”，也必须满足：

- 仅管理员可触发；
- 必须二次确认；
- 安装动作单独走主机维护流程，不混在普通容器创建请求里。

## 二、容器创建支持 GPU

创建容器时，如启用 GPU，则 Agent 在 `docker run` 增加：

```bash
--gpus device=0,1
```

若启用 GPU 且保持默认全选，则直接使用：

```bash
--gpus all
```

同时建议补充环境变量：

```bash
-e NVIDIA_VISIBLE_DEVICES=0,1
-e NVIDIA_DRIVER_CAPABILITIES=compute,utility
```

实现原则：

- 前端必须显式勾选“启用 GPU”；
- 启用后默认选中全部 GPU；
- 允许用户手动取消部分 GPU；
- 若服务器不支持 GPU，则禁止提交；
- 同一 GPU 可被多个容器复用，不做冲突拦截。

## 三、容器创建支持可写层磁盘限额

当前代码没有容器磁盘大小限制。

现状判断依据：

- `agent.py` 创建容器时未传 `--storage-opt size=...`；
- `app.py` 也未保存或透传类似 `rootfs_limit` 的字段。

计划新增：

- 容器可写层限额字段 `rootfs_limit`；
- 创建时透传到 Agent；
- Agent 在支持的服务器上使用：

```bash
--storage-opt size=120G
```

注意事项：

- 该限额只作用于容器可写层；
- 挂载目录、bind mount、Docker volume 不受此限制；
- 不是所有 Docker 存储驱动都支持该能力。

Agent 需要在 `/checks` 或 `/sysinfo` 中增加能力探测：

- 当前 `Storage Driver`；
- 对 `--storage-opt size` 是否支持；
- 若使用 `overlay2`，需额外检查 backing filesystem 是否为 `xfs` 且挂载了 `pquota`；
- 若不支持，则前端禁用该字段并明确提示。

## 四、容器实时指标采集

建议新增接口：

```text
GET /containers/metrics
```

返回当前 Agent 管理的所有容器实时指标。

原因：

- 一次请求可以批量拿到所有容器指标；
- 比对每个容器单独请求更省；
- 更适合前端列表轮询。

### CPU 与内存

优先方案：

- 使用 `docker stats --no-stream --format '{{json .}}'`

采集：

- CPU 百分比；
- 内存已用；
- 内存限制。

说明：

- 这是现阶段最简单、侵入最小的方案；
- 后续若需要更高精度，再切 cgroup 原始统计。

### 磁盘占用

使用：

```bash
docker inspect --size <container>
```

读取：

- `SizeRw`

只展示容器可写层，不统计：

- bind mount；
- Docker volume；
- 宿主机数据盘。

### GPU 设备与进程归因

优先使用 NVML，而不是长期解析 `nvidia-smi` 文本。

建议 Agent 侧引入：

- `pynvml`

采集流程：

1. 读取服务器 GPU 列表；
2. 读取每张 GPU 当前整卡利用率和整卡显存占用；
3. 读取该 GPU 上的运行进程列表；
4. 获取每个 GPU 进程的 `pid` 与 `usedGpuMemory`；
5. 通过 `/proc/<pid>/cgroup` 将宿主机进程归因到 Docker 容器；
6. 将同一容器的多个 GPU 进程聚合。

### GPU 利用率归因

首选：

- 使用 NVML 进程级利用率采样接口。

如果驱动或设备支持，则按进程取样后聚合到容器：

```text
容器 GPU 利用率 = 容器内所有 GPU 进程利用率之和
```

如果宿主机不支持进程级利用率采样，则降级为：

- 仍展示容器 GPU 显存占用；
- 仍展示整卡利用率；
- 容器级 GPU 利用率字段标记为 `unsupported` 或 `null`。

这样前端可以明确显示：

```text
GPU 利用率：当前驱动不支持按容器精确归因
```

这是比伪造数字更稳妥的做法。

## 面板侧实施方案

## 一、后端 API 代理层

中心面板新增两类代理接口：

```text
GET /api/servers/<sid>/gpu
GET /api/containers/metrics
```

职责：

- 从对应 Agent 拉取 GPU 能力与实时容器指标；
- 按服务器聚合；
- 转成前端稳定格式；
- 不写入 `data.json`。

`/api/containers/metrics` 建议返回：

- `container_id`
- `server_id`
- CPU / 内存 / 磁盘指标
- GPU 指标
- `collected_at`

## 二、创建容器弹窗

在现有“分配容器”弹窗中新增 GPU 区域：

- 是否启用 GPU；
- 目标服务器 GPU 列表；
- GPU 选择方式：多选；
- 默认值：启用后自动全选全部 GPU；
- 说明文字：GPU 为共享访问，不做独占。

交互规则：

1. 先选服务器；
2. 自动读取该服务器 GPU 能力；
3. 若服务器不支持 GPU，则 GPU 区域置灰；
4. 若支持 GPU，则显示所有 GPU 列表与当前共享情况；
5. 提交时将 `gpu_enabled / gpu_devices / gpu_mode` 一并发给后端。

同时新增可写层磁盘限额字段：

- 字段名：`容器磁盘上限（可写层）`
- 默认值：建议 `120g`
- 仅在服务器支持 `--storage-opt size` 时可编辑
- 不支持时置灰并显示原因

## 三、容器管理页与资源卡片

### 列表页

不建议直接扩成很多独立列，否则会太乱。

建议在容器操作区或容器主信息下增加一行紧凑指标：

```text
CPU 12% | MEM 3.4 / 8 GB | DISK 1.2 GB | GPU 0,1 | VRAM 6.5 GB | GPU 37%
```

### 资源卡片

在“修改资源”卡片中增加只读实时状态区：

- 当前 CPU
- 当前内存
- 当前磁盘可写层占用
- 当前 GPU 使用情况

编辑区仍只允许修改：

- CPU 限制
- 内存限制
- PIDs 限制

GPU 配置在卡片中只展示，不允许原地修改。

如需变更 GPU，提供单独入口：

```text
重建容器并调整 GPU 配置
```

磁盘限额也采用同样边界：

- 在资源卡片中显示当前 `可写层磁盘上限`；
- 若服务器支持该能力，则在卡片中放到“需重建生效”区块，而不是“在线修改”区块；
- 用户修改后，保存动作走重建流程，不走 `docker update`。

## 四、刷新策略

建议刷新策略如下：

- 容器管理页激活时：立即拉一次；
- 页面停留期间：每 5 秒拉一次；
- 切离页面：停止轮询；
- 资源卡片打开时：额外立即刷新一次当前容器指标。

## 容器重建策略

以下字段变化必须触发重建：

- 镜像；
- 端口；
- 挂载；
- 登录用户；
- GPU 设备列表；
- GPU 启用状态；
- 可写层磁盘限额；
- 其他需要重启生效的安全参数。

重建流程沿用既有思路：

```text
1. 保留旧容器；
2. 按新配置创建临时容器；
3. 复用原挂载目录和必要 volume；
4. 验证新容器启动成功；
5. 删除旧容器；
6. 新容器重命名为正式名称；
7. 失败则回滚。
```

## 合并后的实施顺序

## 第一阶段：能力探测 + 实时监控基础

目标：

- 一次性补齐资源监控的基础能力；
- 明确哪些服务器支持 GPU、哪些服务器支持可写层磁盘限额；
- 先把容器列表的动态指标跑通。

交付：

- Agent `GET /containers/metrics`
- Agent `GET /gpu/info`
- Agent `/checks` 或 `/sysinfo` 返回存储驱动与磁盘限额支持状态
- 面板 `GET /api/containers/metrics`
- 面板 `GET /api/servers/<sid>/gpu`
- 容器页显示 `CPU / MEM / DISK`

## 第二阶段：创建容器支持共享 GPU + 磁盘限额

目标：

- 创建容器时支持共享 GPU；
- 启用 GPU 时默认全量分配；
- 创建容器时支持可写层磁盘限额。

交付：

- 前端 GPU 多选，默认全选全部 GPU
- 后端与 Agent 透传 `gpu_*` 配置
- Agent 支持 `--gpus all` / `--gpus device=...`
- 前端新增 `容器磁盘上限（可写层）`
- 后端与 Agent 透传 `rootfs_limit`
- Agent 在支持环境下使用 `--storage-opt size=...`
- 容器静态配置保存 `gpu_*` 与 `rootfs_limit`

## 第三阶段：GPU 归因 + 更新卡片与重建流程

目标：

- 完成容器级 GPU 归因展示；
- 把“在线修改”和“需重建生效”的边界在 UI 上一次理清；
- 支持修改 GPU 与磁盘限额时的重建流程。

交付：

- 容器级 GPU 显存统计
- 支持时显示容器级 GPU 利用率
- 不支持时明确降级提示
- 更新卡片拆分为：
  - 在线修改区：CPU / 内存 / PIDs
  - 需重建区：GPU 配置 / 可写层磁盘限额
- 后端走临时容器重建流程并保留挂载数据

## 验收标准

### 资源监控

1. 容器管理页能稳定显示 `CPU / MEM / DISK`。
2. 磁盘值只统计容器可写层，不随挂载目录大小变化。
3. 刷新 30 分钟后前端无明显卡顿，面板进程无异常写盘增长。

### GPU 能力

1. GPU 服务器能正确列出所有 GPU。
2. 非 GPU 服务器在创建容器时自动禁用 GPU 区域。
3. 创建启用 GPU 的容器后，容器内 `nvidia-smi` 可见对应设备。
4. 启用 GPU 后，如用户不手动调整，默认可见全部 GPU。

### 磁盘限额

1. 当前代码默认无磁盘限额，新增后可在支持的服务器上生效。
2. 容器写入超过限额时，仅容器可写层受限，挂载目录仍可正常写入。
3. 不支持 `--storage-opt size` 的服务器会在前端明确禁用该字段。
4. 修改磁盘限额时走重建流程，不走在线 `docker update`。

### GPU 监控

1. 多个容器共享同一 GPU 时，能分别看到各自显存占用。
2. 若驱动支持进程级利用率采样，则能看到容器级 GPU 利用率。
3. 若驱动不支持，则前端明确显示“不支持精确归因”，而不是展示假数据。

## 风险与降级策略

### 1. 进程级 GPU 利用率并非所有环境都支持

策略：

- 支持则展示容器级 `GPU%`；
- 不支持则仅展示：
  - 容器 GPU 显存占用；
  - GPU 整卡利用率；
  - “当前环境不支持容器级 GPU 利用率精确归因”提示。

### 2. `docker stats` 轮询有性能开销

策略：

- 批量拉取；
- 页面不激活时停止；
- 默认 5 秒轮询，不做 1 秒级刷新。

### 3. PID 到容器映射可能受运行时差异影响

策略：

- 优先从 `/proc/<pid>/cgroup` 解析容器 ID；
- 若失败，再结合 Docker inspect / label 信息补充；
- 映射失败的 GPU 进程归入“unknown”，不强行记到某个容器。

### 4. 磁盘限额受 Docker 存储驱动约束

策略：

- Agent 启动时先探测能力，再决定是否开放该字段；
- 若服务器不满足条件，则只展示“当前环境不支持可写层磁盘限额”；
- 不在不支持的环境下强行注入 `--storage-opt size`。

## 涉及文件

预计后续改动将主要落在：

- `dockerhub-manager/agent/agent.py`
- `dockerhub-manager/app.py`
- `dockerhub-manager/templates/dashboard.html`

如需补充部署能力，可能还会涉及：

- `dockerhub-manager/deploy.sh`

## 当前阶段不做

- 不做 GPU 独占锁；
- 不做 GPU 硬配额；
- 不默认启用 NVIDIA MPS；
- 不在 `data.json` 中保存实时监控值；
- 不为了展示 GPU 利用率而编造估算值；
- 不把挂载目录大小算进容器磁盘占用；
- 不把可写层磁盘限额伪装成可在线热更新能力。
