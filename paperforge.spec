# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成 PaperForge 桌面版 .exe。

打包命令：
    pyinstaller paperforge.spec --noconfirm

产物：dist/PaperForge/PaperForge.exe（单文件模式可直接分发）

注意：
- datas 中前端静态资源指向 web/dist（npm run build 后生成）
- 运行时 .exe 会启动 FastAPI 服务并自动打开浏览器
"""
import sys
from pathlib import Path

block_cipher = None

# 项目根目录（spec 文件所在目录）
project_root = Path(SPECPATH).resolve()
web_dist = project_root / 'web' / 'dist'

a = Analysis(
    ['mock_api/launcher.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # 前端静态资源：打包后放在 web/dist 下，launcher 用 StaticFiles 挂载
        (str(web_dist / '*'), 'web/dist'),
        # 种子数据等
        ('mock_api/data.py', 'mock_api'),
    ],
    hiddenimports=[
        'mock_api',
        'mock_api.main',
        'mock_api.llm',
        'mock_api.llm.factory',
        'mock_api.llm.openai_provider',
        'mock_api.llm.zhipu_provider',
        'mock_api.llm.deepseek_provider',
        'zhipuai',
        'openai',
        'pypdf',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PaperForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=True,  # 桌面版保留控制台窗口便于查看日志
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='paperforge.ico',
)
