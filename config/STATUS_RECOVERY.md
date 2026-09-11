# 状态监测接入的恢复发布

正常发布仍使用各应用的 GitHub Actions 和 Flux。Smart Shop 的跨境镜像上传曾超过一小时；需要恢复发布时，使用 `scripts/publish-smart-shop-recovery.py` 在现有服务器完成同一提交的构建和上传。

脚本只允许 Smart Shop 后端镜像，要求精确提交和 `1.0.N` 标签。构建前读取 GitHub 的后端、前端成功检查，再逐文件核对源代码的 Git blob 摘要。构建限用 1 核 CPU、768 MiB 内存；不会修改 Kubernetes、业务数据库或部署清单。推送使用已有根目录凭据文件和临时 Docker 配置，拒绝覆盖已有版本标签。

操作顺序：

1. 在已核对的 Smart Shop 仓库中生成源代码归档：`git archive --format=tar --output=smart-shop-source.tar <完整提交> backend docker .dockerignore`。
2. 将归档和本仓库已提交的发布脚本复制到服务器 `/var/tmp/`。
3. 运行 `python3 /var/tmp/publish-smart-shop-recovery.py build --revision <完整提交> --tag <本次流水线标签> --archive /var/tmp/smart-shop-source.tar`。
4. 构建成功后取消仍在上传的对应 GitHub Actions 运行，避免同时发布同一版本。
5. 运行 `python3 /var/tmp/publish-smart-shop-recovery.py push --revision <完整提交> --tag <本次流水线标签>`。
6. 由 Flux 发现镜像、更新 Git 和部署。验收 Pod 就绪、报告鉴权、公开组件状态及运行版本。

若基础镜像下载也缓慢，构建阶段可附加 `--runtime-base <当前正式版标签>`。脚本会核对正式版来源和已通过的检查，并要求 Dockerfile、入口脚本、Python 依赖及构建排除规则完全不变，再按不可变镜像摘要复用运行环境，完整替换应用源码。任何运行环境输入变化都会拒绝复用；该路径不会顺带升级系统或 Python 依赖。

没有配置 `STATUS_MONITOR_TOKEN` 时，应用的报告接口关闭，普通功能独立运行。恢复发布脚本也不要求其他应用依赖 Status Page。

2026-09-12 已实际验证：提交 `f2efa2913784cc5715d726021018b27349c34cc3` 复用正式版 `1.0.28` 的未变运行环境，完成受限构建和镜像内鉴权、三项组件检查。发布 `1.0.29`，镜像摘要为 `sha256:6497708fad7231880def3350e260fab8ba3be55b5c67c6c1b5811b81cbdfa95c`；原 GitHub Actions 的测试已通过，耗时较长的上传运行已取消。
