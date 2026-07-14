# GPU Web 服务安装

服务使用安装了 PaddleOCR GPU 依赖的 Python 环境运行，并固定为单实例、单进程。请先确认该环境中的 `nvidia-smi`、PaddlePaddle GPU 和项目 CLI 均可用。

## 安装与启动

将下列占位符替换为服务器实际路径和用户：

- `/ABSOLUTE/PATH/TO/bid_source/bid-document-extractor`：本仓库绝对路径。
- `/ABSOLUTE/PATH/TO/GPU_ENV`：已安装 GPU 版 PaddlePaddle/PaddleOCR 的 Python 环境。
- `YOUR_USER`：运行服务且可访问 GPU、仓库和输出目录的系统用户。

```bash
cd /ABSOLUTE/PATH/TO/bid_source/bid-document-extractor
/ABSOLUTE/PATH/TO/GPU_ENV/bin/python -m pip install -r requirements.txt
sudo cp deploy/bid-document-extractor.env.example /etc/bid-document-extractor.env
sudo cp deploy/bid-document-extractor.service.example /etc/systemd/system/bid-document-extractor.service
sudoedit /etc/bid-document-extractor.env
sudoedit /etc/systemd/system/bid-document-extractor.service
sudo systemctl daemon-reload
sudo systemctl enable --now bid-document-extractor.service
sudo systemctl status bid-document-extractor.service
sudo journalctl -u bid-document-extractor.service -f
```

浏览器访问 `http://172.20.0.160:8000`。

`/etc/bid-document-extractor.env` 中的 `BID_SOURCE_ROOT` 必须指向仓库父目录。例如服务器仓库为 `/bwopt/MODELS/hj/bid_source_v1/bid-document-extractor` 时，应配置：

```bash
BID_SOURCE_ROOT=/bwopt/MODELS/hj/bid_source_v1
```

运行数据与仓库保持同级：

```text
bid_source_v1/
├── bid-document-extractor/  # 代码仓库
├── data/                    # 输入和配置
├── outputs/                 # 解析结果
└── service_data/            # 任务数据库、上传、日志、锁和 ZIP 缓存
```

修改该路径后，先停止服务并删除仓库内部的旧运行数据，再重新验证：

```bash
sudo systemctl stop bid-document-extractor.service
cd /bwopt/MODELS/hj/bid_source_v1/bid-document-extractor
rm -rf -- outputs service_data data
cd ..
mkdir -p outputs service_data data/raw data/configs
sudo systemctl start bid-document-extractor.service
```

## 安全边界

该服务不提供登录或 HTTPS，只应开放给可信内网。请用服务器防火墙限制 TCP 8000 的来源网段，不要直接暴露到公网。API Key 由浏览器随单次创建请求提交，只保存在服务内存和任务子进程环境中；不要把 Key 写入 env 文件或 systemd unit。

应用依赖进程内的每卡队列与密钥仓，因此不能复制 unit 启动第二实例，也不能改成多进程启动。`service_data/service.lock` 会拒绝第二实例。

## 常用检查

```bash
/ABSOLUTE/PATH/TO/GPU_ENV/bin/python -c "import paddle; print(paddle.device.get_device())"
nvidia-smi
curl http://127.0.0.1:8000/api/system/gpus
sudo systemctl restart bid-document-extractor.service
```

若 GPU 列表失败，先检查 systemd 运行用户是否能执行 `nvidia-smi`。若任务失败，查看网页中的脱敏日志和 `journalctl`；不要通过命令行参数传递 API Key。
