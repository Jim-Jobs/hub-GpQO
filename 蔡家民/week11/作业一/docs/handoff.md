# 你接下来只需配置真实模型服务

当前不依赖模型服务的开发、数据准备与本地核验已经完成。无需重新编写代码或安装第三方Python包。

## 第一步：进入项目目录

```powershell
cd 'C:\Users\15853\hub-GpQO\蔡家民\week11\作业一'
```

## 第二步：配置你自己的模型

```powershell
.\scripts\configure_model.ps1
```

按提示输入：

1. 服务Base URL：使用供应商的API地址，通常带版本路径；不要填写网页聊天地址，也不要包含/chat/completions。
2. 模型名称：供应商账户实际支持的模型标识。
3. API Key：隐藏输入。脚本不会打印或写入文件，只设置当前终端环境。

若本机PowerShell策略不允许运行脚本，可按README中的手动环境变量方法配置，无需修改系统策略。

配置预检不会发送模型请求；它只能检查本地配置完整性，不能证明密钥有效或供应商接口兼容。当前终端关闭后需重新配置。

## 第三步：先运行3题小样本

```powershell
python scripts/run_experiments.py --mode live --split dev --limit 3 --execute
```

这会比较同一批开发题的基线与完整工作流，设置共享请求上限60次；真实调用会产生供应商用量。默认不启用第二个裁判模型。报告保存到reports/experiment_live_summary.json。

你也可以在配置好后告知我模型名称和小样本结果，我再继续分析；不要把API Key发到聊天中。仅在你自己的终端设置变量并不会自动把配置传给其他终端。

本地完整验收、数据检查和离线演示已经执行过，不需要你重做；如需复现，用python scripts/verify.py。
