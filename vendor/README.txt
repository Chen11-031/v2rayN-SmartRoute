sing-box 1.12.9（Windows amd64）
================================

用途
----
v2rayN 7.10.4 生成的 sing-box 配置仍使用旧版 DNS 字段、dns 特殊出站和 inbound sniff 字段。
sing-box 1.13.0 已移除这些字段，导致 Hysteria2/TUIC 节点无法启动。
SmartRoute 在启用这类节点时，会把 v2rayN 自带的 1.13+ 核心备份后替换为本文件，
并在启动 v2rayN 时附加兼容环境变量。

来源
----
https://github.com/SagerNet/sing-box/releases/download/v1.12.9/sing-box-1.12.9-windows-amd64.zip

官方压缩包 SHA-256：
f9f9b55d394fe08a8afe1fd3bb1c037e687a12fbe701f16481be6941d1766840

本目录 sing-box-1.12.9.exe SHA-256：
5c94a0a25b1b61b2f50c00815e72d6f3e5e5d234e6adf7d8a02526b5b173bc79

许可证
------
sing-box 以 GPLv3 发布，许可证声明见 LICENSE.sing-box.txt。
GPLv3 全文：https://www.gnu.org/licenses/gpl-3.0.html
对应源代码（v1.12.9）：https://github.com/SagerNet/sing-box/tree/v1.12.9
本目录中的二进制为官方发布包，未做修改。
