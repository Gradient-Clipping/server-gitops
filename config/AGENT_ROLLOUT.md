# Sandbox 模板发布

固定的 Agent Sandbox v1.0.2 会同步既有 Pod 的标签和注解，但不会更新其 Pod spec；[上游控制器仍保留该 TODO](https://github.com/kubernetes-sigs/agent-sandbox/blob/v1.0.2/controllers/sandbox_controller.go#L1265)。因此单纯更改 `podTemplate.spec.containers[].image` 无法完成部署。

`agent-rollout` CronJob 每分钟运行一次受管脚本，只检查 `lazycampus-agent/campus-sandbox`。首次接入核对实际 Pod 的镜像、命令、参数、环境、资源和卷配置，采用命名列表精确匹配，并处理 Kubernetes 默认字段、资源量等价写法、默认 requests、标准 ServiceAccount 投射卷和既定 RuntimeClass 的节点选择。初次配置一致则登记当前指纹，不额外重建。

后续模板指纹或上述实际配置变化时，任务先在 Sandbox 对象元数据中记录待应用模板及旧 Pod UID，然后带 UID/resourceVersion 前置条件删除这个 Pod，由原 Sandbox 控制器从最新模板重建。指纹不写入 `podTemplate`，避免控制器将新哈希同步到旧 Pod 后误判已发布。新 Pod 的 UID 和配置核对成功后才记录 applied；中途失败可在下一次定时运行继续。

任务只拥有指定 Sandbox 的 get/patch 和指定 Pod 的 get/delete 权限，没有 Secrets、exec、PVC、PV 或其他 Pod 权限，也不挂载工作区。删除的是运行 Pod，四个独立 PVC、Retain PV 和宿主数据目录不受影响。进程会按 Pod 正常终止宽限期退出；单实例发布期间有短暂停机，正在执行的请求可能中断。

通过 GitOps 修改 Sandbox 顶层注解 `platform.lazycampus.com/sandbox-rollout: paused` 可暂停替换；移除该注解即可恢复。`spec.operatingMode` 非 Running 或对象正在删除时不会操作 Pod。轮换 Secret 的值不会改变模板；如需新进程读取更新后的环境变量，在 Git 中同步更新 `spec.podTemplate.metadata.annotations` 下自定义的重启版本标记。运行脚本的 `--dry-run` 只读检查，不写状态或删除 Pod。

RuntimeClass `agent-gvisor` 当前注入 `kubernetes.io/hostname: easy-platform-1`，脚本按这份受管配置识别默认节点约束。未来改变 RuntimeClass 调度约束时，应同步更新脚本的默认值。CronJob 单次最多 90 秒，无并行运行，保留 1 次成功和 2 次失败记录。
