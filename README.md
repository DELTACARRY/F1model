# F1 赛车模型：历史数据库 × 实时比价

这是一个**仅供个人本地使用**的 F1 / 赛车模型检索平台，不考虑公网部署、多用户、账号系统或 SEO。

## 核心结构

### 1. 历史模型库 `models`
回答“这个模型是什么、历史上有哪些版本”。

主要字段：品牌、车手、车队、赛季、车型、底盘代号、比赛/GP、特别版、比例、产品类型、SKU、限量数量、官方图片、官方页面、备注、核验状态。

品牌范围固定为：**MINI GT / Spark / BBR / Minichamps / Looksmart / Bell**。

### 2. 实时商品 `market_listings`
回答“现在谁在卖、多少钱”。每一条线上 listing 可关联到一个历史模型，不会因为不同卖家而重复创建模型。

字段包括：平台、标题、链接、卖家、成色、购买方式、标价、币种、运费、估算人民币到手价、图片、首次发现、最后发现。

### 3. 价格历史 `price_history`
每次检索到同一商品时追加一个价格点，为后续 30 / 90 / 365 天行情曲线准备数据。

### 4. 别名 `aliases`
给历史模型增加中文俗称、缩写、特别版关键词，例如 `白牛 / White Livery / 7冠`，用于网页商品自动匹配。

### 5. 监控规则 `watchlist`
已经预留表结构，后续可把当前 W11 / RB16B / RB21 等监控规则直接迁入。

## 网页入口

- **模型库**：按车手 / 车型 / 代号 / 特别版 / SKU / 品牌 / 比例 / 赛季检索；支持本地新增模型。
- **实时比价**：自由搜索或从某个历史模型直接发起检索；自动保存 listing 与价格点。
- **模型详情**：左侧历史档案，右侧当前已关联市场行情与最低/平均价格。

## 数据文件

第一次启动后自动生成：

```text
f1_models.db
```

这是 SQLite 本地数据库，和 `app.py` 放在同一目录，所有历史模型与行情都保存在本机。

## 运行

```bash
cd f1_model_price_compare
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

浏览器打开：

```text
http://127.0.0.1:8765
```

## 实时检索配置

### eBay

```powershell
$env:EBAY_CLIENT_ID="你的ClientID"
$env:EBAY_CLIENT_SECRET="你的ClientSecret"
```

### Yahoo!拍卖 / Mercari / Bunjang / 闲鱼等网页检索层

```powershell
$env:GOOGLE_CSE_API_KEY="你的APIKey"
$env:GOOGLE_CSE_CX="你的SearchEngineID"
```

没有 API Key 时网页仍正常运行，并提供各平台的一键搜索入口。

## 当前版本的重要行为

- “全部品牌”也只接受六个目标品牌。
- `MINIGT` 自动归一为 `MINI GT`；`MINIchanmp / Minichamp` 自动归一为 `Minichamps`。
- 从“模型详情”发起实时搜索时，检索结果会明确绑定该历史模型。
- 自由搜索会尝试与历史模型库做关键词匹配；匹配可信度不足时不会强行绑定。
- 每次获得实时结果，都会写入 `market_listings`，并向 `price_history` 追加观测点。
- 当前网页摘要价格仍属于“估价”，结构化 API 价格与网页摘要价格在 UI 中分开标记。

## 下一阶段

1. 从六个品牌官网批量导入完整历史产品数据与官方图片。
2. 增强模型识别器：车手 / 赛车 / 底盘 / 比赛 / 冠军版 / 特别涂装 / SKU。
3. 为闲鱼建立更专门的价格与拍卖状态解析。
4. 增加 30 / 90 / 365 天价格曲线和“当前价相对历史均价”的偏离度。
5. 把现有监控条件正式写入 `watchlist`，形成首页监控面板。

## 新品 / 中古与倒卖评估

实时商品现在额外记录：

- `condition_group`：归一化为 **新品 / 中古 / 未知**。结构化平台优先使用官方成色字段，网页搜索结果则从标题与摘要识别。
- `prior_purchase_price / prior_purchase_cny`：如果中古商品的公开标题或摘要明确写出“购入价 / 購入価格 / 구매가 / paid / purchased for”等真实购买措辞，则记录卖家当时购入价格；没有明确证据时显示“—”。**原价、定价、MSRP、定価不会被当成购买价。**
- `flip_reference_cny`：同一历史模型、同成色可比 listing 的挂牌价中位数。
- `flip_net_spread_cny`：参考中位价减去当前到手价，再减去默认交易摩擦预留。
- `flip_margin_pct`：净空间 / 当前到手价。
- `flip_rating`：A 值得关注 / B 有空间 / C 空间有限 / D 不建议倒卖 / 数据不足。
- `flip_confidence`：按同成色可比样本数量给出低 / 中 / 高。

当前默认交易摩擦预留为 **参考价的 8%，且最低 ¥50**，用于覆盖议价、平台摩擦、包装和二次运费等不确定成本。这只是个人筛选参数，不代表任何平台的真实费率，也不等于最终净利润。

网页“实时比价”页可切换：**新品 + 中古 / 只看新品 / 只看中古**。模型详情页会分别显示新品和中古的样本数与中位价，避免混价判断。
