"""
统一的消息处理模块
用于处理与智谱AI实时音视频API的WebSocket通信消息
支持音频、视频、函数调用和服务端VAD等多种场景
"""

import os
import time
import base64
#import wave
import asyncio
import json
import sounddevice as sd
import subprocess
from collections.abc import Callable
from typing import Any, Optional

from rtclient import RTLowLevelClient
from rtclient.models import (
    InputAudioBufferClearMessage,
    FunctionCallOutputItem,
    ItemCreateMessage,
)

from lelamp.service.rgb.rgb_service import RGBService
from lelamp.service.motors.animation_service import AnimationService


class AudioFileSaver:
    def __init__(self, filename, rate):
        self.filename = filename
        self.rate = rate
        self.frames = []
        self.is_recording = False

    def start(self):
        self.frames = []
        self.is_recording = True
        print(f"开始录制 AI 语音到文件: {self.filename}")

    def add_frame(self, frame_bytes):
        if self.is_recording:
            self.frames.append(frame_bytes)

    def stop(self):
        if not self.is_recording:
            return
        
        self.is_recording = False
        if not self.frames:
            print("没有收到音频数据")
            return

        #print(f"正在保存文件，共 {len(self.frames)} 个数据块...")
        self.audio_data = b''.join(self.frames)  # 保存为实例属性，方便后续播放
        print(f"音频数据总大小: {len(self.audio_data)} 字节")
        
        """
        # 将所有数据块拼接成完整的二进制数据
        audio_data = b''.join(self.frames)
        print(f"保存的音频文件总大小: {len(audio_data)}")
        
        # 保存为 WAV 文件
        with wave.open(self.filename, 'wb') as wf:
            wf.setnchannels(1)       # 单声道
            wf.setsampwidth(2)       # 16bit (2 bytes)
            wf.setframerate(self.rate)
            wf.writeframes(audio_data)
        print(f"文件已保存: {os.path.abspath(self.filename)}")
        """
        
    def play_with_aplay(self):
        """用aplay直接播放裸PCM数据（无需保存文件）"""
        if not hasattr(self, 'audio_data') or len(self.audio_data) == 0:
            print("无音频数据可播放")
            return
        
        # aplay参数说明：
        # -r：采样率  -c：声道数  -f：格式（S16_LE=16位小端序PCM）
        cmd = [
            'aplay',
            '-r', str(self.rate),
            '-D', 'plughw:1,0',
            '-f', 'S16_LE',
        ]
        
        try:
            # 启动aplay进程，将音频数据通过stdin传入
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,  # 标准输入管道
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            # 传入PCM数据并等待播放完成
            stdout, stderr = proc.communicate(input=self.audio_data)
            
            if proc.returncode != 0:
                print(f"aplay播放失败: {stderr.decode('utf-8')}")
            else:
                print("音频播放完成（aplay）")
                self.client.send(InputAudioBufferClearMessage())
        except FileNotFoundError:
            print("未找到aplay命令，请安装alsa-utils：sudo apt install alsa-utils")
        except Exception as e:
            print(f"播放出错: {e}")

    def play_with_sounddevice(self):
        """用sounddevice播放裸PCM字节流（跨平台）"""
        if not hasattr(self, 'audio_data') or len(self.audio_data) == 0:
            print("无音频数据可播放")
            return
        
        # 将字节流转换为numpy数组（sounddevice要求的格式）
        # 16bit PCM → int16数组
        audio_np = np.frombuffer(self.audio_data, dtype=np.int16)
        
        print(f"开始播放音频（时长：{len(audio_np)/self.rate:.2f}秒）...")
        # 播放音频（阻塞直到播放完成）
        sd.play(audio_np, samplerate=self.rate, channels=1, device=1)
        sd.wait()  # 等待播放结束
        print("音频播放完成（sounddevice）")

OUTPUT_FILENAME = "/home/pi/Documents/source/python/glm-realtime-sdk/python/ai_response.wav"
SAMPLE_RATE = 24000  # GLM-4-Voice 默认输出采样率通常是 24000Hz
saver = AudioFileSaver(OUTPUT_FILENAME, SAMPLE_RATE)


class MessageHandler:
    def __init__(self, client: RTLowLevelClient, shutdown_event: Optional[asyncio.Event] = None):
        """
        初始化消息处理器
        Args:
            client: WebSocket客户端实例
            shutdown_event: 关闭事件，用于控制消息接收的终止
        """
        self.client = client
        self.shutdown_event = shutdown_event
        self._custom_handlers: dict[str, Callable] = {}
        
        self.rgb_service = RGBService()
        
        self.anim_service = AnimationService(
            port="/dev/ttyACM0",
            lamp_id="bubble"
        )
        
        # 2. 启动服务（必须启动，否则 dispatch 会忽略事件）
        self.rgb_service.start()
        self.anim_service.start()
        print("启动灯光服务")
        
        self.rgb_service.dispatch("solid", (255, 0, 0))
        #rainbow = [(255,0,0), (255,127,0), (255,255,0), (0,255,0), (0,0,255), (75,0,130), (148,0,211)]*9
        #self.rgb_service.dispatch("paint", rainbow[:64])
        time.sleep(0.5)
        self.rgb_service.dispatch("solid", (0, 255, 0))
        time.sleep(0.5)
        self.rgb_service.dispatch("solid", (0, 0, 255))
        time.sleep(0.5)
        self.rgb_service.clear()

    def register_handler(self, message_type: str, handler: Callable):
        """
        注册自定义消息处理器
        Args:
            message_type: 消息类型
            handler: 处理函数，接收message参数
        """
        self._custom_handlers[message_type] = handler

    async def _handle_function_call(self, message: Any):
        """处理函数调用消息"""
        print("函数调用参数完成消息")
        print(f"  Response Id: {message.response_id}")
        if hasattr(message, "name"):
            print(f"  Function Name: {message.name}")
        print(f"  Arguments: {message.arguments if message.arguments else 'None'}")
                
        # 开灯
        if hasattr(message, "name") and message.name == "lightOn":
            try:
                args = json.loads(message.arguments)
                self.rgb_service.dispatch("solid", (255, 255, 255))
                response = {"status": "success", "message": f"成功打开灯光"}
                await asyncio.sleep(1)

                output_item = FunctionCallOutputItem(output=json.dumps(response, ensure_ascii=False))
                create_message = ItemCreateMessage(item=output_item)
                await self.client.send(create_message)
                await self.client.send_json({"type": "response.create"})
            except json.JSONDecodeError as e:
                print(f"解析函数调用参数失败: {e}")
                
        # 关灯
        if hasattr(message, "name") and message.name == "lightOff":
            try:
                response = {"status": "success", "message": f"成功关闭灯光"}
                self.rgb_service.dispatch("solid", (0, 0, 0))
                await asyncio.sleep(1)

                output_item = FunctionCallOutputItem(output=json.dumps(response, ensure_ascii=False))
                create_message = ItemCreateMessage(item=output_item)
                await self.client.send(create_message)
                await self.client.send_json({"type": "response.create"})
            except json.JSONDecodeError as e:
                print(f"解析函数调用参数失败: {e}")
                
        # 红色
        if hasattr(message, "name") and message.name == "lightRed":
            try:
                response = {"status": "success", "message": f"已将灯光设置成红色"}
                self.rgb_service.dispatch("solid", (255, 0, 0))
                await asyncio.sleep(1)

                output_item = FunctionCallOutputItem(output=json.dumps(response, ensure_ascii=False))
                create_message = ItemCreateMessage(item=output_item)
                await self.client.send(create_message)
                await self.client.send_json({"type": "response.create"})
            except json.JSONDecodeError as e:
                print(f"解析函数调用参数失败: {e}")
                
        # 摇头晃脑
        if hasattr(message, "name") and message.name == "actionNegitive":
            try:
                response = {"status": "success", "message": f"不是的"}
                self.anim_service.dispatch("play", "negitive")
                await asyncio.sleep(1)

                output_item = FunctionCallOutputItem(output=json.dumps(response, ensure_ascii=False))
                create_message = ItemCreateMessage(item=output_item)
                await self.client.send(create_message)
                await self.client.send_json({"type": "response.create"})
            except json.JSONDecodeError as e:
                print(f"解析函数调用参数失败: {e}")
                
        # 扫描查看四周
        if hasattr(message, "name") and message.name == "actionLookAround":
            try:
                response = {"status": "success", "message": f"好的，我看看四周有什么好东西"}
                self.anim_service.dispatch("play", "look_around")
                await asyncio.sleep(1)

                output_item = FunctionCallOutputItem(output=json.dumps(response, ensure_ascii=False))
                create_message = ItemCreateMessage(item=output_item)
                await self.client.send(create_message)
                await self.client.send_json({"type": "response.create"})
            except json.JSONDecodeError as e:
                print(f"解析函数调用参数失败: {e}")
                
        # Happy, Yes
        if hasattr(message, "name") and message.name == "actionYes":
            try:
                response = {"status": "success", "message": f"是的，确实是这样"}
                self.anim_service.dispatch("play", "yes")
                await asyncio.sleep(1)

                output_item = FunctionCallOutputItem(output=json.dumps(response, ensure_ascii=False))
                create_message = ItemCreateMessage(item=output_item)
                await self.client.send(create_message)
                await self.client.send_json({"type": "response.create"})
            except json.JSONDecodeError as e:
                print(f"解析函数调用参数失败: {e}")

    async def _handle_session_messages(self, message: Any, msg_type: str):
        """处理会话相关消息"""
        match msg_type:
            case "session.created":
                print("会话创建消息")
                print(f"  Session Id: {message.session.id}")
            case "session.updated":
                print("会话更新消息")
                print(f"updated session: {message.session}")
            case "error":
                print("错误消息")
                print(f"  Error: {message.error}")

    async def _handle_audio_input_messages(self, message: Any, msg_type: str):
        """处理音频输入相关消息"""
        match msg_type:
            case "input_audio_buffer.committed":
                print("音频缓冲区提交消息")
                if hasattr(message, "item_id"):
                    print(f"  Item Id: {message.item_id}")
            case "input_audio_buffer.speech_started":
                print("语音开始消息")
            case "input_audio_buffer.speech_stopped":
                print("语音结束消息")

    async def _handle_conversation_messages(self, message: Any, msg_type: str):
        """处理会话项目相关消息"""
        match msg_type:
            case "conversation.item.created":
                print("会话项目创建消息")
            case "conversation.item.input_audio_transcription.completed":
                print("输入音频转写完成消息")
                print(f"  Transcript: {message.transcript}")

    async def _handle_response_messages(self, message: Any, msg_type: str):
        """处理响应相关消息"""
        match msg_type:
            case "response.created":
                print("响应创建消息")
                print(f"  Response Id: {message.response.id}")
                saver.start() # 开始准备录制
            case "response.done":
                print("响应完成消息")
                if hasattr(message, "response"):
                    print(f"  Response Id: {message.response.id}")
                    print(f"  Status: {message.response.status}")
            case "response.audio.delta":
                print("模型音频增量消息")
                print(f"  Response Id: {message.response_id}")
                if message.delta:
                    print(f"  Delta Length: {len(message.delta)}")
                    try:
                        # 1. 解码 Base64 数据
                        audio_bytes = base64.b64decode(message.delta)
                        # 2. 将数据交给保存器
                        saver.add_frame(audio_bytes)
                        # 这里也可以选择同时播放音频，如果不需要播放可以注释掉
                        #os.system()
                    except Exception as e:
                        print(f"处理音频数据出错: {e}")
                else:
                    print("  Delta: None")
            case "response.audio.done":
                print("模型音频完成消息")
                try:
                    saver.stop()
                    print("Audio file saved")
                    #time.sleep(0.5)
                    #os.system(f"aplay {OUTPUT_FILENAME}")
                    #await asyncio.get_event_loop().run_in_executor(None, saver.play_with_aplay)  # 直接播放，不保存WAV
                    saver.play_with_aplay()
                except Exception as e:
                    print(f"处理音频数据出错: {e}")
            case "response.audio_transcript.delta":
                print("模型音频文本增量消息")
                print(f"  Response Id: {message.response_id}")
                print(f"  Delta: {message.delta if message.delta else 'None'}")
            case "response.audio_transcript.done":
                print("模型音频文本完成消息")

    async def receive_messages(self):
        """
        统一的消息接收处理函数
        处理所有类型的消息，包括音频、视频、函数调用和服务端VAD等场景
        """
        try:
            while not self.client.closed:
                if self.shutdown_event and self.shutdown_event.is_set():
                    print("正在停止消息接收...")
                    break

                try:
                    message = await asyncio.wait_for(self.client.recv(), timeout=5.0)
                    if message is None:
                        continue

                    msg_type = message.type if hasattr(message, "type") else message.get("type")
                    if msg_type is None:
                        print("收到未知类型的消息:", message)
                        continue

                    # 检查是否有自定义处理器
                    if msg_type in self._custom_handlers:
                        await self._custom_handlers[msg_type](message)
                        continue

                    if msg_type.startswith("session.") or msg_type == "error":
                        await self._handle_session_messages(message, msg_type)
                    elif msg_type.startswith("input_audio_buffer."):
                        await self._handle_audio_input_messages(message, msg_type)
                    elif msg_type.startswith("conversation.item."):
                        await self._handle_conversation_messages(message, msg_type)
                    elif msg_type.startswith("response."):
                        if msg_type == "response.function_call_arguments.done":
                            await self._handle_function_call(message)
                        else:
                            await self._handle_response_messages(message, msg_type)
                    elif msg_type == "heartbeat":
                        print("心跳消息")
                    else:
                        print(f"未处理的消息类型: {msg_type}")
                        print(message)

                except TimeoutError:
                    continue  # 超时后继续尝试接收
                except Exception as e:
                    if not self.shutdown_event or not self.shutdown_event.is_set():
                        print(f"接收消息时发生错误: {e}")
                    break
        finally:
            print("正在释放硬件资源...")
            try:
                self.rgb_service.stop()
            except Exception as e:
                print(f"RGB service 停止出错: {e}")

            try:
                print("停止电机")
                self.anim_service.stop()
            except Exception as e:
                print(f"停止电机出错：{e}")

            if not self.client.closed:
                await self.client.close()
                print("WebSocket连接已关闭")


async def create_message_handler(
    client: RTLowLevelClient, shutdown_event: Optional[asyncio.Event] = None
) -> MessageHandler:
    """
    创建消息处理器实例
    Args:
        client: WebSocket客户端实例
        shutdown_event: 关闭事件
    Returns:
        MessageHandler实例
    """
    #my_led.breath_effect()
    return MessageHandler(client, shutdown_event)
