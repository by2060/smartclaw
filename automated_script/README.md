
---
当前版本 v2026.5.14

前置条件：机器需联网
### 1、如何使用
1. 将automated_script文件夹放入flocks项目根目录
2. 进入一个python虚拟环境
   - 优先smartclaw环境，如果是其他环境执行脚本失败，缺包安包

### 2、工具先后执行顺序：
1. 隐藏前端弹窗与内置菜单：
   `python automated_script/toggle_home_prompts.py --disable`
2. 去flocks+去rex+修改思考头像+修改浏览器链接标签头像：
   `python automated_script/rebrand_smartclaw.py --apply`
3. 删除后端内置资源（子agent、工作流、skill、工具清单）:
   `python automated_script/curate_builtin_plugins.py --apply`
4. 构建前端静态资源：
```bash
   cd webui
   npm ci  # 如果没有package-lock.json文件，改用 `npm install`
   npm run build
```
5. 删除项目多余目录或文件：
   `python automated_script/prune_project.py --apply`
6. 查看项目中是否存在Flocks、flocks、Rex、rex字样：
   `python automated_script/check_flocks_keyword.py`
7. 此步可选，根据需要选择是否执行：
   代码编译（编译后默认项目名为[delivery_build]，可自行修改所需项目名）：
   `python automated_script/build_delivery_artifact.py --apply`

### 3、注意事项
⚠️ 如果未进行编译，在执行完以上工具并且生效后，请将 automated_script 文件夹从项目中删掉！


