#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPU服务器监控系统
提供实时的CPU、内存、GPU使用情况监控
安装依赖：python3 -m pip install --user flask psutil
"""

import json
import subprocess
import psutil
import socket
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify
import threading
import time
import os

app = Flask(__name__)

# HTML模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GPU服务器监控 - {{ hostname }}</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Arial', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        
        .header h1 {
            font-size: 2.5rem;
            margin-bottom: 10px;
        }
        
        .last-update {
            opacity: 0.8;
            font-size: 1.1rem;
        }
        
        .content {
            padding: 30px;
        }
        
        .system-info {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .info-card {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            border-left: 4px solid #3498db;
        }
        
        .info-card h3 {
            color: #2c3e50;
            margin-bottom: 15px;
            font-size: 1.2rem;
        }
        
        .info-value {
            font-size: 1.8rem;
            font-weight: bold;
            color: #e74c3c;
        }
        
        .progress-bar {
            width: 100%;
            height: 20px;
            background-color: #ecf0f1;
            border-radius: 10px;
            overflow: hidden;
            margin-top: 10px;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #27ae60, #f1c40f, #e74c3c);
            transition: width 0.3s ease;
            border-radius: 10px;
        }
        
        .gpu-section {
            margin-top: 30px;
        }
        
        .gpu-card {
            background: white;
            border-radius: 15px;
            margin-bottom: 20px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        
        .gpu-header {
            background: linear-gradient(135deg, #8e44ad 0%, #9b59b6 100%);
            color: white;
            padding: 20px;
            font-size: 1.3rem;
            font-weight: bold;
        }
        
        .gpu-info {
            padding: 20px;
        }
        
        .gpu-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        
        .gpu-stat {
            text-align: center;
        }
        
        .gpu-stat-label {
            color: #7f8c8d;
            font-size: 0.9rem;
            margin-bottom: 5px;
        }
        
        .gpu-stat-value {
            font-size: 1.4rem;
            font-weight: bold;
            color: #2c3e50;
        }
        
        .processes-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        
        .processes-table th,
        .processes-table td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ecf0f1;
        }
        
        .processes-table th {
            background-color: #f8f9fa;
            font-weight: bold;
            color: #2c3e50;
        }
        
        .processes-table tr:hover {
            background-color: #f8f9fa;
        }
        
        .no-processes {
            text-align: center;
            color: #7f8c8d;
            font-style: italic;
            padding: 20px;
        }
        
        .refresh-btn {
            position: fixed;
            bottom: 30px;
            right: 30px;
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
            color: white;
            border: none;
            border-radius: 50px;
            padding: 15px 25px;
            font-size: 1rem;
            cursor: pointer;
            box-shadow: 0 5px 15px rgba(52, 152, 219, 0.3);
            transition: transform 0.2s ease;
        }
        
        .refresh-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(52, 152, 219, 0.4);
        }
        
        .status-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
        }
        
        .status-free { background-color: #27ae60; }
        .status-busy { background-color: #e74c3c; }
        .status-partial { background-color: #f1c40f; }
        
        @media (max-width: 768px) {
            .container {
                margin: 10px;
                border-radius: 10px;
            }
            
            .header {
                padding: 20px;
            }
            
            .header h1 {
                font-size: 2rem;
            }
            
            .content {
                padding: 20px;
            }
            
            .system-info {
                grid-template-columns: 1fr;
            }
            
            .gpu-stats {
                grid-template-columns: repeat(2, 1fr);
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🖥️ GPU服务器监控</h1>
            <div class="last-update">服务器: {{ hostname }} | 最后更新: <span id="lastUpdate">{{ last_update }}</span></div>
        </div>
        
        <div class="content">
            <!-- 系统信息 -->
            <div class="system-info">
                <div class="info-card">
                    <h3>🔥 CPU使用率</h3>
                    <div class="info-value">{{ cpu_percent }}%</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: {{ cpu_percent }}%;"></div>
                    </div>
                </div>
                
                <div class="info-card">
                    <h3>💾 内存使用</h3>
                    <div class="info-value">{{ memory_percent }}%</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: {{ memory_percent }}%;"></div>
                    </div>
                    <div style="margin-top: 10px; font-size: 0.9rem; color: #7f8c8d;">
                        {{ memory_used }} / {{ memory_total }}
                    </div>
                </div>
                
                <div class="info-card">
                    <h3>💿 磁盘使用</h3>
                    <div class="info-value">{{ disk_percent }}%</div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: {{ disk_percent }}%;"></div>
                    </div>
                    <div style="margin-top: 10px; font-size: 0.9rem; color: #7f8c8d;">
                        {{ disk_used }} / {{ disk_total }}
                    </div>
                </div>
                
                <div class="info-card">
                    <h3>⏱️ 系统负载</h3>
                    <div class="info-value">{{ load_avg }}</div>
                    <div style="margin-top: 10px; font-size: 0.9rem; color: #7f8c8d;">
                        1分钟平均负载
                    </div>
                </div>
            </div>
            
            <!-- GPU信息 -->
            <div class="gpu-section">
                <h2 style="margin-bottom: 20px; color: #2c3e50;">🎮 GPU使用情况</h2>
                
                {% for gpu in gpus %}
                <div class="gpu-card">
                    <div class="gpu-header">
                        <span class="status-indicator status-{{ gpu.status }}"></span>
                        GPU {{ gpu.id }}: {{ gpu.name }}
                    </div>
                    <div class="gpu-info">
                        <div class="gpu-stats">
                            <div class="gpu-stat">
                                <div class="gpu-stat-label">GPU使用率</div>
                                <div class="gpu-stat-value">{{ gpu.utilization }}%</div>
                            </div>
                            <div class="gpu-stat">
                                <div class="gpu-stat-label">显存使用</div>
                                <div class="gpu-stat-value">{{ gpu.memory_used }} / {{ gpu.memory_total }}</div>
                            </div>
                            <div class="gpu-stat">
                                <div class="gpu-stat-label">显存使用率</div>
                                <div class="gpu-stat-value">{{ gpu.memory_percent }}%</div>
                            </div>
                            <div class="gpu-stat">
                                <div class="gpu-stat-label">温度</div>
                                <div class="gpu-stat-value">{{ gpu.temperature }}°C</div>
                            </div>
                        </div>
                        
                        {% if gpu.processes %}
                        <table class="processes-table">
                            <thead>
                                <tr>
                                    <th>进程ID</th>
                                    <th>用户</th>
                                    <th>进程名</th>
                                    <th>显存占用</th>
                                    <th>运行时间</th>
                                    <th>命令</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for process in gpu.processes %}
                                <tr>
                                    <td>{{ process.pid }}</td>
                                    <td><strong>{{ process.username }}</strong></td>
                                    <td>{{ process.name }}</td>
                                    <td>{{ process.gpu_memory }}</td>
                                    <td>{{ process.runtime }}</td>
                                    <td style="max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                        {{ process.command }}
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                        {% else %}
                        <div class="no-processes">
                            🎉 此GPU当前空闲，无进程占用
                        </div>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>
    
    <button class="refresh-btn" onclick="location.reload()">🔄 刷新</button>
    
    <script>
        // 自动刷新页面
        setInterval(function() {
            location.reload();
        }, 30000); // 30秒刷新一次
        
        // 更新时间显示
        function updateTime() {
            document.getElementById('lastUpdate').textContent = new Date().toLocaleString('zh-CN');
        }
        
        // 每秒更新时间
        setInterval(updateTime, 1000);
    </script>
</body>
</html>
"""

def get_hostname():
    """获取主机名"""
    return socket.gethostname()

def get_system_info():
    """获取系统基本信息"""
    # CPU使用率
    cpu_percent = round(psutil.cpu_percent(interval=1), 1)
    
    # 内存信息
    memory = psutil.virtual_memory()
    memory_percent = round(memory.percent, 1)
    memory_used = f"{memory.used / (1024**3):.1f} GB"
    memory_total = f"{memory.total / (1024**3):.1f} GB"
    
    # 磁盘信息
    disk = psutil.disk_usage('/')
    disk_percent = round(disk.percent, 1)
    disk_used = f"{disk.used / (1024**3):.1f} GB"
    disk_total = f"{disk.total / (1024**3):.1f} GB"
    
    # 系统负载
    load_avg = os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0
    
    return {
        'cpu_percent': cpu_percent,
        'memory_percent': memory_percent,
        'memory_used': memory_used,
        'memory_total': memory_total,
        'disk_percent': disk_percent,
        'disk_used': disk_used,
        'disk_total': disk_total,
        'load_avg': round(load_avg, 2)
    }

def get_process_runtime(pid):
    """获取进程运行时间"""
    try:
        process = psutil.Process(pid)
        create_time = datetime.fromtimestamp(process.create_time())
        runtime = datetime.now() - create_time
        
        days = runtime.days
        hours, remainder = divmod(runtime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}天 {hours}时{minutes}分"
        elif hours > 0:
            return f"{hours}时{minutes}分"
        else:
            return f"{minutes}分{seconds}秒"
    except:
        return "未知"

def get_process_command(pid):
    """获取进程完整命令"""
    try:
        process = psutil.Process(pid)
        cmdline = process.cmdline()
        if cmdline:
            return ' '.join(cmdline)
        else:
            return process.name()
    except:
        return "未知"

def get_gpu_info():
    """获取GPU信息"""
    gpus = []
    
    try:
        # 使用nvidia-smi获取GPU信息
        result = subprocess.run([
            'nvidia-smi', 
            '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu',
            '--format=csv,noheader,nounits'
        ], capture_output=True, text=True, check=True)
        
        gpu_lines = result.stdout.strip().split('\n')
        
        for line in gpu_lines:
            if line.strip():
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 6:
                    gpu_id = parts[0]
                    gpu_name = parts[1]
                    utilization = parts[2]
                    memory_used_mb = int(parts[3])
                    memory_total_mb = int(parts[4])
                    temperature = parts[5]
                    
                    memory_percent = round((memory_used_mb / memory_total_mb) * 100, 1)
                    memory_used_gb = f"{memory_used_mb / 1024:.1f} GB"
                    memory_total_gb = f"{memory_total_mb / 1024:.1f} GB"
                    
                    # 获取GPU进程信息
                    processes = get_gpu_processes(gpu_id)
                    
                    # 判断GPU状态
                    if not processes:
                        status = "free"
                    elif int(utilization) > 80:
                        status = "busy"
                    else:
                        status = "partial"
                    
                    gpus.append({
                        'id': gpu_id,
                        'name': gpu_name,
                        'utilization': utilization,
                        'memory_used': memory_used_gb,
                        'memory_total': memory_total_gb,
                        'memory_percent': memory_percent,
                        'temperature': temperature,
                        'processes': processes,
                        'status': status
                    })
    
    except subprocess.CalledProcessError:
        # 如果nvidia-smi不可用
        return [{
            'id': '0',
            'name': 'NVIDIA GPU信息不可用',
            'utilization': '0',
            'memory_used': '0 GB',
            'memory_total': '0 GB',
            'memory_percent': 0,
            'temperature': '0',
            'processes': [],
            'status': 'free'
        }]
    except Exception as e:
        return [{
            'id': '0',
            'name': f'获取GPU信息失败: {str(e)}',
            'utilization': '0',
            'memory_used': '0 GB',
            'memory_total': '0 GB',
            'memory_percent': 0,
            'temperature': '0',
            'processes': [],
            'status': 'free'
        }]
    
    return gpus

def get_gpu_processes(gpu_id):
    """获取指定GPU的进程信息"""
    processes = []
    
    try:
        # 获取GPU进程信息
        result = subprocess.run([
            'nvidia-smi', 
            '--query-compute-apps=pid,process_name,used_memory',
            '--format=csv,noheader,nounits',
            f'--id={gpu_id}'
        ], capture_output=True, text=True, check=True)
        
        process_lines = result.stdout.strip().split('\n')
        
        for line in process_lines:
            if line.strip() and 'No running' not in line:
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 3:
                    pid = int(parts[0])
                    process_name = parts[1]
                    gpu_memory = f"{parts[2]} MB"
                    
                    # 获取进程的用户信息
                    try:
                        process = psutil.Process(pid)
                        username = process.username()
                        runtime = get_process_runtime(pid)
                        command = get_process_command(pid)
                        
                        processes.append({
                            'pid': pid,
                            'name': process_name,
                            'username': username,
                            'gpu_memory': gpu_memory,
                            'runtime': runtime,
                            'command': command
                        })
                    except psutil.NoSuchProcess:
                        # 进程可能已经结束
                        continue
                    except Exception:
                        # 无法获取进程信息，使用默认值
                        processes.append({
                            'pid': pid,
                            'name': process_name,
                            'username': '未知',
                            'gpu_memory': gpu_memory,
                            'runtime': '未知',
                            'command': '未知'
                        })
    
    except subprocess.CalledProcessError:
        pass
    except Exception:
        pass
    
    return processes

@app.route('/')
def index():
    """主页面"""
    hostname = get_hostname()
    system_info = get_system_info()
    gpu_info = get_gpu_info()
    last_update = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    return render_template_string(HTML_TEMPLATE, 
                                hostname=hostname,
                                last_update=last_update,
                                cpu_percent=system_info['cpu_percent'],
                                memory_percent=system_info['memory_percent'],
                                memory_used=system_info['memory_used'],
                                memory_total=system_info['memory_total'],
                                disk_percent=system_info['disk_percent'],
                                disk_used=system_info['disk_used'],
                                disk_total=system_info['disk_total'],
                                load_avg=system_info['load_avg'],
                                gpus=gpu_info)

@app.route('/api/status')
def api_status():
    """API接口，返回JSON格式的系统状态"""
    hostname = get_hostname()
    system_info = get_system_info()
    gpu_info = get_gpu_info()
    
    return jsonify({
        'hostname': hostname,
        'timestamp': datetime.now().isoformat(),
        'system': system_info,
        'gpus': gpu_info
    })

def check_dependencies():
    """检查依赖是否安装"""
    missing_deps = []
    
    # 检查psutil
    try:
        import psutil
    except ImportError:
        missing_deps.append('psutil')
    
    # 检查flask
    try:
        import flask
    except ImportError:
        missing_deps.append('flask')
    
    # 检查nvidia-smi
    try:
        subprocess.run(['nvidia-smi', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("警告: nvidia-smi不可用，GPU信息可能无法正常显示")
    
    if missing_deps:
        print("缺少以下Python包，请安装:")
        for dep in missing_deps:
            print(f"  pip install {dep}")
        return False
    
    return True

if __name__ == '__main__':
    print("=== GPU服务器监控系统 ===")
    print("正在检查依赖...")
    
    if not check_dependencies():
        print("请先安装缺少的依赖包")
        exit(1)
    
    print("依赖检查完成")
    
    # 获取本机IP
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    port = 8082
    
    print(f"\n🚀 启动监控服务...")
    print(f"📡 服务器: {hostname}")
    print(f"🌐 访问地址:")
    print(f"   本地访问: http://localhost:{port}")
    print(f"   内网访问: http://{local_ip}:{port}")
    print(f"📊 API接口: http://{local_ip}:{port}/api/status")
    print(f"⏰ 页面每30秒自动刷新")
    print(f"\n按 Ctrl+C 停止服务\n")
    
    try:
        app.run(host='0.0.0.0', port=port, debug=False)
    except KeyboardInterrupt:
        print("\n👋 服务已停止")
    except Exception as e:
        print(f"❌ 启动失败: {e}")
