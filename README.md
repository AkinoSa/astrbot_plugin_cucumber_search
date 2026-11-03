# astrbot_plugin_cucumber_search (睦睦搜图 / 黄瓜搜图)

<div align="center">

![睦睦搜图](logo.png)

> 🥒 **基于多引擎聚合的 Coser 社媒账号与二次元图源反查 AstrBot 插件**

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-green.svg)](https://github.com/Soulter/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20FNOS-orange.svg)](#)

![访客统计](https://m.h4ck.org.cn/get/@astrbot_plugin_cucumber_search?theme=rule34)

</div>

给一张图，反查图中人物是哪个 Coser、社交账号在哪（Twitter/X、微博、B站、Instagram、小红书等），或查二次元原插画/番剧出处！

---

## ✨ 核心特性

- **多引擎聚合反查**：
  - 🔍 **Google Lens**：识别 Coser 最准，智能爬取推特/微博主页链接。
  - 🎨 **SauceNAO**：专查二次元同人插画、Pixiv 画师出处。
  - 🎬 **trace.moe**：动画截屏溯源，精准定位番剧名称、集数与精确秒数。
  - 🖼️ **IQDB**：二次元高清壁纸与图库检索。
  - 🇨🇳 **百度识图**：国内中文互联网图源补充。
- **零额外 NPM 依赖**：内置 Node 引擎纯原生手搓，不装任何庞大的 npm 包，轻量快速。
- **全平台智能适配**：
  - **Windows**：自动探测 Edge / Chrome；
  - **Linux / Docker**：自动探测系统 Chromium；
  - **飞牛私有云 (FNOS)**：完美开箱即用，自动对接应用商店的 `nodejs_v22` 与 `Chrome 浏览器` (fygo-browser)。
- **全界面可配置化**：所有触发词、别名、表情包、引擎开关、代理地址均可在 AstrBot 管理面板视窗中随时修改生效。
- **支持引用回复**：群友发了一张图，直接**引用该图片**并发送 `/睦睦搜图` 即可快速反查。
- **图文合并与人设立体化**：支持表情包与结果合并为一条消息发送；支持接入会话 LLM 人格重新润色回复及上下文记忆。

---

## 🚀 安装方法

### 方式一：AstrBot WebUI 面板安装（推荐）
1. 打开 AstrBot 管理后台 -> **插件市场**。
2. 搜索 `astrbot_plugin_cucumber_search` 并点击安装。
3. 点击 **重载插件** 即可。

### 方式二：手动 Git Clone 安装
进入你的 AstrBot 插件目录：
```bash
cd data/plugins/
git clone https://github.com/AkinoSa/astrbot_plugin_cucumber_search.git
```
在 AstrBot 控制台中重新载入插件。

---

## 📖 使用方法

1. **引用查图（最常用）**：
   - 引用群里某人发的一张图，回复：
     ```text
     /睦睦搜图
     ```
   - 或者使用别名：
     ```text
     /搜图
     /找coser
     /查出处
     /睦睦找鸡
     /黄瓜搜图
     ```
2. **带图发送**：
   - 发送一张图片的同时附带文字 `/睦睦搜图`。

### 输出示例

```text
[表情包]
🎯 睦睦搜图结果

🌐 平台：Twitter
👤 名字：阿薰kaOri
🆔 账号：@izayoill · https://x.com/izayoill

🌐 平台：Weibo
👤 名字：阿薰kaOri_
🆔 账号：https://weibo.com/u/1234567890
```

---

## ⚙️ 插件配置项一览

在 AstrBot 管理面板 -> **插件设置** 中，可随心调整以下参数：

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| **触发主指令** | `睦睦搜图` | 触发命令词（前面带不带 `/` 均可） |
| **指令别名** | `搜图,找coser,查出处,睦睦找鸡,黄瓜搜图` | 多个别名用逗号隔开，均可触发 |
| **Google Lens** | `开启` | 最准，查 Coser 必备（需代理和浏览器） |
| **SauceNAO** | `开启` | 动漫同人图最准（直连可用） |
| **trace.moe** | `开启` | 动漫番剧截图溯源（直连可用） |
| **网络代理** | `http://127.0.0.1:7890` | 访问 Google 等外网的 HTTP 代理 |
| **冷却时间** | `30` 秒 | 单用户防刷间隔 |
| **命中表情包** | `开启` | 成功查到出处时在结果前附带一张表情包 |
| **表情包路径/URL** | `/vol2/1000/meme/success.jpg` | 支持填本地图片路径或直接填网络图片链接 |

---

## 🛠️ 环境依赖说明

- **Node.js**：版本 `>= 20`（现代系统自带 `fetch` 与 `WebSocket`）。
- **浏览器**（仅 `Google Lens` / `百度识图` 需要）：
  - **Windows**：系统自带 Edge 或 Chrome 即可；
  - **Linux / NAS**：安装了 Chromium 或 Chrome；如果在**飞牛 OS**上，只需在应用商店安装“Chrome 浏览器”和“Node.js”即可自动匹配！

---

## 📄 许可证

本项目基于 [MIT](LICENSE) 协议开源。
