# 扩展字库来源

- 基础字库：用户提供的 CJK Medians / hanzi-writer-data，沿用 Arphic Public License；生成目录附原始 ARPHICPL.TXT。
- NormStroke：<https://github.com/octycs/norm-stroke>，作者声明为 CC0 1.0：<https://creativecommons.org/publicdomain/zero/1.0/>。
- 固定源文件：`font/NormStroke.svg`，SHA-256 `acc05c1a18fa3528ca4c70c0847d65b9a319dbafa38fa7f1a60436cd3d243fbc`。原 SVG metadata 保留来源与声明。
- 转换：SVG 单线笔画的椭圆弧离散为折线，统一单位与 Y 方向，保留大小写和标点的垂直位置。
- 项目补绘：NormStroke 未含的 ASCII `^`、反引号、竖线，以及中文标点、度数、摄氏度、千分号和欧元符号；由几何中心线构造。全角 ASCII 使用同一字形、全角字格。
- 具体新增字符、基础文件哈希及修改日期随生成结果记录在 `coverage.json`。新增字形与原始汉字一同按基础字库的 Arphic Public License 提供。
