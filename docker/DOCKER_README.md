
---

前置条件：
1、机器需联网
2、git clone 拉取代码，先执行自动化工具进行去flocks化等操作
- 参考 automated_script 目录中的README

### 1、如何使用
1. 进入项目根打包镜像：docker build -f docker/Dockerfile . -t smartclaw:latest
2. 导出镜像：docker save -o smartclaw-image.tar smartclaw:latest
3. 导入镜像：docker load -i smartclaw-image.tar
4. 按部署手册操作即可
