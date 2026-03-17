# AI Lamp

这是一个基于LeLamp台灯修改的开源项目。完善了LeLamp中缺少的视觉功能，接入了国产模型：GLM智普大模型，对中文的理解能力更好。

修改了原代码在树莓派3B上的部分问题。

## 概述

LeLamp Runtime is a Python-based control system that interfaces with the hardware components of LeLamp including:

- Servo motors for articulated movement
- Audio system (microphone and speaker)
- RGB LED lighting
- Camera system
- Voice interaction capabilities

## 代码结构

```
lelamp_runtime/
├── lelamp_main.py         # 主程序入口
├── message_handler.py	   # 大模型消息处理
├── pyproject.toml         # 项目配置文件和安装依赖
├── lelamp/                # Core package
│   ├── setup_motors.py    # 电机管理和设置
│   ├── calibrate.py       # 电机校准
│   ├── list_recordings.py # 动作列表
│   ├── record.py          # 动作捕捉
│   ├── replay.py          # 动作回放
│   ├── follower/          # follower模式
│   ├── leader/            # Leader模式
│   └── test/              # 硬件单元测试
└── uv.lock                # uv文件
```

## 运行环境安装

