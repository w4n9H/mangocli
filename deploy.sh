#!/bin/bash

# 项目名称
project="mangopi-cli"
version=$(python -c "with open('version.py') as f: print([line.split('=')[1].strip().strip('\"') for line in f if '__version__' in line][0])")
echo "$project: $version"

# 清理dist目录
echo "Clean dist / build"
rm -rf ./dist/*
rm -rf ./build/*
rm -rf *.egg-info

# 卸载当前安装的项目版本
echo "Uninstall ${project}"
pip uninstall -y ${project}

# 构建项目
echo "Build ${project} ${version}"
python -m build

# 安装新构建的项目版本
echo "Install ${project} ${version}"
pip install .

echo "Upload ${project} ${version}"
twine upload dist/*