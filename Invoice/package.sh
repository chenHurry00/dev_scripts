#!/bin/bash
# 快速部署打包脚本

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
WITH_DATA=false

# 解析参数
if [ "$1" == "--with-data" ]; then
    WITH_DATA=true
    PACKAGE_NAME="invoice-system-with-data-${TIMESTAMP}.tar.gz"
else
    PACKAGE_NAME="invoice-system-${TIMESTAMP}.tar.gz"
fi

echo "=================================="
echo "发票报销系统 - 打包部署"
echo "=================================="

if [ "$WITH_DATA" = true ]; then
    echo "模式: 包含数据（数据库+附件+日志）"
else
    echo "模式: 仅程序文件"
fi
echo ""

# 创建临时目录
TEMP_DIR=$(mktemp -d)
DEPLOY_DIR="$TEMP_DIR/invoice-system"
mkdir -p "$DEPLOY_DIR"

echo "正在打包..."

# 复制必要文件
cp "$SCRIPT_DIR/app.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/README.md" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/start.sh" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/setup_firewall.sh" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/install_service.sh" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/uninstall_service.sh" "$DEPLOY_DIR/"

# 如果包含数据，复制数据库和附件
if [ "$WITH_DATA" = true ]; then
    echo "  ✓ 复制程序文件"

    # 复制数据库
    if [ -f "$SCRIPT_DIR/invoice.db" ]; then
        cp "$SCRIPT_DIR/invoice.db" "$DEPLOY_DIR/"
        echo "  ✓ 复制数据库 ($(du -h "$SCRIPT_DIR/invoice.db" | cut -f1))"
    fi

    # 复制附件
    if [ -d "$SCRIPT_DIR/uploads" ]; then
        cp -r "$SCRIPT_DIR/uploads" "$DEPLOY_DIR/"
        UPLOAD_SIZE=$(du -sh "$SCRIPT_DIR/uploads" | cut -f1)
        echo "  ✓ 复制附件 ($UPLOAD_SIZE)"
    fi

    # 复制日志（可选）
    if [ -d "$SCRIPT_DIR/logs" ]; then
        cp -r "$SCRIPT_DIR/logs" "$DEPLOY_DIR/"
        echo "  ✓ 复制日志"
    fi

    # 复制备份（可选）
    if [ -d "$SCRIPT_DIR/backup" ] && [ "$(ls -A "$SCRIPT_DIR/backup")" ]; then
        cp -r "$SCRIPT_DIR/backup" "$DEPLOY_DIR/"
        echo "  ✓ 复制备份文件"
    fi
else
    echo "  ✓ 复制程序文件（不含数据）"
fi

# 创建部署说明
cat > "$DEPLOY_DIR/INSTALL.txt" << EOF
发票报销系统 - 快速部署指南

$(if [ "$WITH_DATA" = true ]; then echo "【包含数据】此包包含数据库、附件和日志"; else echo "【仅程序】此包仅包含程序文件，不含数据"; fi)

部署步骤：

1. 解压文件
   tar -xzf $(basename "$PACKAGE_NAME")
   cd invoice-system

2. 安装依赖
   pip3 install -r requirements.txt

$(if [ "$WITH_DATA" = false ]; then echo "3. 修改管理员密码
   vim app.py  # 修改顶部 ADMIN_PASSWORD"; else echo "3. 数据已包含，无需修改密码（使用原密码）"; fi)

4. 启动服务
   # 临时启动
   ./start.sh

   # 或安装为系统服务（开机自启）
   chmod +x *.sh
   sudo ./install_service.sh

5. 开放端口
   sudo ./setup_firewall.sh

6. 访问系统
   http://服务器IP:5000

$(if [ "$WITH_DATA" = true ]; then echo "
数据说明：
- 数据库: invoice.db
- 附件: uploads/
- 日志: logs/
- 备份: backup/

注意：解压后数据会覆盖目标目录中的同名文件！"; fi)

详细文档：查看 README.md
EOF

# 打包
cd "$TEMP_DIR"
tar -czf "$SCRIPT_DIR/$PACKAGE_NAME" invoice-system/
cd "$SCRIPT_DIR"

# 清理临时目录
rm -rf "$TEMP_DIR"

echo "✓ 打包完成: $PACKAGE_NAME"
echo ""

# 显示文件信息
FILE_SIZE=$(du -h "$PACKAGE_NAME" | cut -f1)
echo "文件大小: $FILE_SIZE"
echo "保存位置: $SCRIPT_DIR/$PACKAGE_NAME"

echo ""
echo "=================================="
echo "部署到其他服务器："
echo "=================================="
echo "1. 传输文件:"
echo "   scp $PACKAGE_NAME user@server:/opt/"
echo ""
echo "2. 在目标服务器解压:"
echo "   cd /opt"
echo "   tar -xzf $PACKAGE_NAME"
echo "   cd invoice-system"
echo ""
echo "3. 查看部署说明:"
echo "   cat INSTALL.txt"
echo ""
echo "4. 安装依赖:"
echo "   pip3 install -r requirements.txt"
echo ""
if [ "$WITH_DATA" = false ]; then
echo "5. 修改配置:"
echo "   vim app.py  # 修改顶部管理员密码"
echo ""
echo "6. 启动服务:"
else
echo "5. 启动服务:"
fi
echo "   ./start.sh  # 临时启动"
echo "   或"
echo "   sudo ./install_service.sh  # 安装为系统服务"
echo ""
if [ "$WITH_DATA" = false ]; then
echo "7. 开放端口:"
else
echo "6. 开放端口:"
fi
echo "   sudo ./setup_firewall.sh"
echo ""
if [ "$WITH_DATA" = false ]; then
echo "8. 访问系统:"
else
echo "7. 访问系统:"
fi
echo "   http://服务器IP:5000"
echo "=================================="

