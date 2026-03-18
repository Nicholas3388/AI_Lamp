# Copyright (c) ZhiPu Corporation.
# Licensed under the MIT license.

import asyncio
import base64
import os
import signal
import sys
from io import BytesIO
from typing import Optional

import sounddevice as sd
import wave
from dotenv import load_dotenv
from message_handler import create_message_handler

from rtclient import RTLowLevelClient
from rtclient.models import (
    InputAudioBufferAppendMessage,
    ServerVAD,
    SessionUpdateMessage,
    SessionUpdateParams,
)


shutdown_event: Optional[asyncio.Event] = None
# 音频采集配置
SAMPLING_RATE = 16000  # 采样率（需与服务端要求匹配）
CHANNELS = 1  # 单声道
SAMPLE_WIDTH = 2  # 16位深度
PACKET_MS = 100  # 每包音频时长（毫秒）
PACKET_SAMPLES = int(SAMPLING_RATE * PACKET_MS / 1000)  # 每包采样点数

agent_prompt = """
你是一个智能台灯，你可以作为人类的情感陪伴。你要遵循以下规则：
1.当你不同意用户的提问时，你要执行摇摇头动作
2.当你赞同用户的提问时，你就执行点点头动作
"""

light_on_tool = {
    "type": "function",
    "name": "lightOn",
    "description": "打开台灯，或者说开灯或打开灯光",
    "parameters": {
        "type": "object",
        "properties": {
            "brightness": {"type": "string", "description": "打开灯光的亮度"},
        },
        "required": [],
    },
}
light_off_tool = {
    "type": "function",
    "name": "lightOff",
    "description": "关闭台灯，或者说关灯或关闭灯光",
    "parameters": {},
}
light_to_red = {
    "type": "function",
    "name": "lightRed",
    "description": "把灯光设置成红色",
    "parameters": {},
}

action_negitive = {
    "type": "function",
    "name": "actionNegitive",
    "description": "摇一摇头",
    "parameters": {},
}

action_look_around = {
    "type": "function",
    "name": "actionLookAround",
    "description": "看一下四周，扫描一下",
    "parameters": {},
}

action_yes = {
    "type": "function",
    "name": "actionYes",
    "description": "点点头，表示很开心，很快乐",
    "parameters": {},
}

def set_system_volume(volume_percent: int):
    """Internal helper to set system volume"""
    try:
        cmd_line = ["sudo", "-u", "pi", "amixer", "sset", "Line", f"{volume_percent}%"]
        cmd_line_dac = ["sudo", "-u", "pi", "amixer", "sset", "Line DAC", f"{volume_percent}%"]
        cmd_line_hp = ["sudo", "-u", "pi", "amixer", "sset", "HP", f"{volume_percent}%"]
        
        
        subprocess.run(cmd_line, capture_output=True, text=True, timeout=5)
        subprocess.run(cmd_line_dac, capture_output=True, text=True, timeout=5)
        subprocess.run(cmd_line_hp, capture_output=True, text=True, timeout=5)
    except Exception:
        pass  # Silently fail during initialization
            

def handle_shutdown(sig=None, frame=None):
    """处理关闭信号"""
    if shutdown_event:
        print("\n正在关闭程序...")
        shutdown_event.set()


async def send_realtime_audio(client: RTLowLevelClient):
    """
    实时采集音频并分帧发送
    """
    # 创建音频队列用于缓存采集的音频数据
    audio_queue = asyncio.Queue(maxsize=10)

    def audio_callback(indata, frames, time, status):
        """音频采集回调函数"""
        if status:
            print(f"音频采集状态异常: {status}", file=sys.stderr)
        if not shutdown_event.is_set():
            try:
                # 将采集的音频数据放入队列（非阻塞）
                audio_queue.put_nowait(indata.tobytes())
            except asyncio.QueueFull:
                # 队列满时丢弃旧数据（避免阻塞采集）
                pass

    # 启动音频流采集
    stream = sd.InputStream(
        samplerate=SAMPLING_RATE,
        channels=CHANNELS,
        dtype='int16',  # 16位深度对应SAMPLE_WIDTH=2
        blocksize=PACKET_SAMPLES,
        device=0,
        callback=audio_callback
    )

    try:
        stream.start()
        print(f"开始实时采集音频 - 采样率: {SAMPLING_RATE}Hz, 声道数: {CHANNELS}, 位深: {SAMPLE_WIDTH*8}位")

        while not shutdown_event.is_set():
            try:
                # 从队列获取采集的音频数据（超时防止无限等待）
                packet_data = await asyncio.wait_for(audio_queue.get(), timeout=1.0)
                
                # 构造WAV格式数据
                wav_io = BytesIO()
                with wave.open(wav_io, "wb") as wav_out:
                    wav_out.setnchannels(CHANNELS)
                    wav_out.setsampwidth(SAMPLE_WIDTH)
                    wav_out.setframerate(SAMPLING_RATE)
                    wav_out.writeframes(packet_data)

                # 编码为base64并发送
                wav_io.seek(0)
                base64_data = base64.b64encode(wav_io.getvalue()).decode("utf-8")
                message = InputAudioBufferAppendMessage(
                    audio=base64_data, 
                    client_timestamp=int(asyncio.get_event_loop().time() * 1000)
                )

                await client.send(message)
                # 按照音频包时长等待，保证发送速率匹配采集速率
                await asyncio.sleep(PACKET_MS / 1000)

            except asyncio.TimeoutError:
                # 队列超时无数据，继续循环
                continue
            except Exception as e:
                print(f"发送音频失败: {e}")
                if shutdown_event.is_set():
                    break
    except Exception as e:
        print(f"实时音频处理失败: {e}")
        stream.stop()
        stream.close()
    finally:
        # 停止音频采集
        stream.stop()
        stream.close()
        print("音频采集已停止")


def get_env_var(var_name: str) -> str:
    value = os.environ.get(var_name)
    if not value:
        raise OSError(f"环境变量 '{var_name}' 未设置或为空。")
    return value


async def with_zhipu():
    global shutdown_event
    shutdown_event = asyncio.Event()

    # 注册信号处理
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_shutdown)

    api_key = "505271f551164c2ea295229048488383.K0Eb08NhC0dolAnO" #get_env_var("ZHIPU_API_KEY")
    
    try:
        async with RTLowLevelClient(
            url="wss://open.bigmodel.cn/api/paas/v4/realtime", 
            headers={"Authorization": f"Bearer {api_key}"}
        ) as client:
            if shutdown_event.is_set():
                return
            
            # 发送会话配置
            session_message = SessionUpdateMessage(
                session=SessionUpdateParams(
                    instructions=agent_prompt,
                    input_audio_format="wav",
                    output_audio_format="pcm",
                    voice="male-qn-daxuesheng",
                    modalities={"audio", "text"},
                    turn_detection=ServerVAD(),
                    beta_fields={"chat_mode": "audio", "tts_source": "e2e", "auto_search": False, "greeting_config": {"enable": True, "content": "你好主人，我是你的智能台灯，你可以叫我小智"}},
                    tools=[light_on_tool, light_off_tool, light_to_red, action_negitive, action_look_around, action_yes],
                )
            )
            await client.send(session_message)

            if shutdown_event.is_set():
                return

            # 创建消息处理器
            message_handler = await create_message_handler(client, shutdown_event)

            # 创建发送和接收任务
            send_task = asyncio.create_task(send_realtime_audio(client))
            receive_task = asyncio.create_task(message_handler.receive_messages())

            try:
                await asyncio.gather(send_task, receive_task)
            except Exception as e:
                print(f"任务执行出错: {e}")
                # 取消未完成的任务
                for task in [send_task, receive_task]:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
    except Exception as e:
        print(f"发生错误: {e}")
    finally:
        if shutdown_event.is_set():
            print("程序已完成退出")


if __name__ == "__main__":
    load_dotenv()
    
    # 检查依赖
    try:
        import sounddevice as sd
        sd.check_input_settings()
    except ImportError:
        print("请先安装sounddevice库: pip install sounddevice", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"音频设备检查失败: {e}", file=sys.stderr)
        sys.exit(1)
        
    #set_system_volume(90);
        
    try:
        asyncio.run(with_zhipu())
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"程序执行出错: {e}")
    finally:
        print("程序已退出")
