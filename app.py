from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import quote_plus

import requests
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
import uvicorn

APP_TITLE = "F1 赛车模型全球比价"

# ---------- FX ----------
FALLBACK_CNY_PER_UNIT = {
    "CNY": 1.0,
    "USD": 7.18,
    "EUR": 8.38,
    "GBP": 9.70,
    "JPY": 0.0485,
    "KRW": 0.00515,
    "SGD": 5.58,
}
_fx_cache = {"ts": 0.0, "rates": FALLBACK_CNY_PER_UNIT.copy(), "source": "fallback"}


def get_fx_rates() -> tuple[dict[str, float], str]:
    """Return CNY value of 1 unit of each currency.

    Tries Frankfurter (ECB-derived) and falls back to editable constants if unavailable.
    """
    now = time.time()
    if now - _fx_cache["ts"] < 3600:
        return _fx_cache["rates"], _fx_cache["source"]
    try:
        # Frankfurter returns: 1 CNY = X foreign currency. Invert to CNY/unit.
        resp = requests.get(
            "https://api.frankfurter.dev/v1/latest",
            params={"base": "CNY", "symbols": "USD,EUR,GBP,JPY,KRW,SGD"},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
        rates = {"CNY": 1.0}
        for cur, per_cny in data.get("rates", {}).items():
            if per_cny:
                rates[cur] = 1.0 / float(per_cny)
        if len(rates) >= 5:
            _fx_cache.update(ts=now, rates=rates, source=f"Frankfurter {data.get('date', '')}".strip())
            return rates, _fx_cache["source"]
    except Exception:
        pass
    _fx_cache.update(ts=now, rates=FALLBACK_CNY_PER_UNIT.copy(), source="fallback")
    return _fx_cache["rates"], _fx_cache["source"]


# ---------- Result model ----------
@dataclass
class Item:
    source: str
    title: str
    url: str
    price: Optional[float] = None
    currency: Optional[str] = None
    shipping: Optional[float] = None
    shipping_currency: Optional[str] = None
    image: Optional[str] = None
    condition: Optional[str] = None
    seller: Optional[str] = None
    buying_option: Optional[str] = None
    total_cny: Optional[float] = None
    price_confidence: str = "structured"


def to_cny(amount: Optional[float], currency: Optional[str], rates: dict[str, float]) -> Optional[float]:
    if amount is None or not currency:
        return None
    rate = rates.get(currency.upper())
    if rate is None:
        return None
    return round(float(amount) * rate, 2)


def normalize_title(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    stop = {"new", "used", "model", "car", "diecast", "spark", "minichamps"}
    words = [w for w in s.split() if w not in stop]
    return " ".join(words[:12])


def dedupe(items: list[Item]) -> list[Item]:
    seen = set()
    out = []
    for item in items:
        key = (item.source, normalize_title(item.title), round(item.total_cny or -1, -1))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ---------- Query expansion ----------
DRIVER_ALIASES = {
    "汉密尔顿": ["Lewis Hamilton", "Hamilton", "ハミルトン", "루이스 해밀턴"],
    "维斯塔潘": ["Max Verstappen", "Verstappen", "フェルスタッペン", "막스 베르스타펜"],
    "勒克莱尔": ["Charles Leclerc", "Leclerc", "ルクレール", "샤를 르클레르"],
}


def expand_query(q: str, scale: str = "", brand: str = "") -> str:
    parts = [q.strip()]
    for zh, aliases in DRIVER_ALIASES.items():
        if zh in q:
            parts.append(aliases[0])
            break
    if scale:
        parts.append(scale.replace(":", ":"))
    if brand and brand.lower() != "all":
        parts.append(brand)
    # Preserve useful F1 tokens while avoiding a huge query.
    return " ".join(dict.fromkeys(p for p in parts if p))


# ---------- Price extraction for web-search fallback ----------
PRICE_PATTERNS = [
    ("JPY", re.compile(r"(?:¥|￥)\s*([0-9][0-9,]*(?:\.\d+)?)")),
    ("JPY", re.compile(r"([0-9][0-9,]*)\s*円")),
    ("KRW", re.compile(r"(?:₩|￦)\s*([0-9][0-9,]*)")),
    ("KRW", re.compile(r"([0-9][0-9,]*)\s*원")),
    ("USD", re.compile(r"(?:US\s*)?\$\s*([0-9][0-9,]*(?:\.\d+)?)", re.I)),
    ("EUR", re.compile(r"€\s*([0-9][0-9,]*(?:\.\d+)?)")),
    ("GBP", re.compile(r"£\s*([0-9][0-9,]*(?:\.\d+)?)")),
    ("CNY", re.compile(r"(?:RMB|CNY|人民币|¥)\s*([0-9][0-9,]*(?:\.\d+)?)", re.I)),
]


def extract_price(text: str, domain: str = "") -> tuple[Optional[float], Optional[str]]:
    # For Japanese domains, treat ¥ as JPY. For Chinese domains, handle CNY first.
    patterns = PRICE_PATTERNS
    if any(d in domain for d in ["goofish.com", "taobao.com", "tmall.com"]):
        patterns = [PRICE_PATTERNS[-1]] + PRICE_PATTERNS[:-1]
    for currency, pat in patterns:
        m = pat.search(text or "")
        if m:
            try:
                return float(m.group(1).replace(",", "")), currency
            except ValueError:
                continue
    return None, None


# ---------- eBay provider ----------
def ebay_token() -> Optional[str]:
    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("access_token")


def search_ebay(q: str, limit: int, rates: dict[str, float]) -> tuple[list[Item], str]:
    token = ebay_token()
    if not token:
        return [], "eBay API key 未配置"
    resp = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        params={"q": q, "limit": min(limit, 50), "filter": "buyingOptions:{FIXED_PRICE|AUCTION}"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": os.getenv("EBAY_MARKETPLACE", "EBAY_US"),
        },
        timeout=12,
    )
    resp.raise_for_status()
    data = resp.json()
    out: list[Item] = []
    for r in data.get("itemSummaries", []):
        p = r.get("price") or {}
        ship_opts = r.get("shippingOptions") or []
        ship_cost = None
        ship_cur = None
        if ship_opts:
            sc = (ship_opts[0] or {}).get("shippingCost") or {}
            if sc.get("value") is not None:
                ship_cost = float(sc["value"])
                ship_cur = sc.get("currency")
        item = Item(
            source="eBay",
            title=r.get("title") or "(无标题)",
            url=r.get("itemWebUrl") or r.get("itemAffiliateWebUrl") or "",
            price=float(p["value"]) if p.get("value") is not None else None,
            currency=p.get("currency"),
            shipping=ship_cost,
            shipping_currency=ship_cur,
            image=(r.get("image") or {}).get("imageUrl"),
            condition=r.get("condition"),
            seller=(r.get("seller") or {}).get("username"),
            buying_option=" / ".join(r.get("buyingOptions") or []),
            price_confidence="structured",
        )
        base = to_cny(item.price, item.currency, rates)
        ship = to_cny(item.shipping, item.shipping_currency or item.currency, rates) or 0
        item.total_cny = round(base + ship, 2) if base is not None else None
        out.append(item)
    return out, f"eBay {len(out)} 条"


# ---------- Google Programmable Search fallback ----------
SOURCES = {
    "yahoo_jp": {"label": "Yahoo!拍卖", "domain": "auctions.yahoo.co.jp"},
    "mercari_jp": {"label": "Mercari JP", "domain": "jp.mercari.com"},
    "rakuma_jp": {"label": "Rakuma", "domain": "fril.jp"},
    "bunjang_kr": {"label": "Bunjang", "domain": "bunjang.co.kr"},
    "joongna_kr": {"label": "Joongna", "domain": "joongna.com"},
    "xianyu_cn": {"label": "闲鱼/Goofish", "domain": "goofish.com"},
    "carousell": {"label": "Carousell", "domain": "carousell.sg"},
}


def google_cse_search(q: str, source_key: str, limit: int, rates: dict[str, float]) -> tuple[list[Item], str]:
    key = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
    cx = os.getenv("GOOGLE_CSE_CX", "").strip()
    source = SOURCES[source_key]
    if not key or not cx:
        return [], f"{source['label']}：Google CSE 未配置"
    query = f"{q} site:{source['domain']}"
    items: list[Item] = []
    start = 1
    while len(items) < min(limit, 20):
        num = min(10, min(limit, 20) - len(items))
        resp = requests.get(
            "https://customsearch.googleapis.com/customsearch/v1",
            params={"key": key, "cx": cx, "q": query, "num": num, "start": start},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("items") or []
        if not batch:
            break
        for r in batch:
            text = f"{r.get('title','')} {r.get('snippet','')}"
            price, currency = extract_price(text, source["domain"])
            total = to_cny(price, currency, rates)
            image = None
            pagemap = r.get("pagemap") or {}
            cse_imgs = pagemap.get("cse_image") or []
            if cse_imgs:
                image = cse_imgs[0].get("src")
            items.append(Item(
                source=source["label"],
                title=r.get("title") or "(无标题)",
                url=r.get("link") or "",
                price=price,
                currency=currency,
                image=image,
                total_cny=total,
                price_confidence="snippet" if price is not None else "unknown",
            ))
        if len(batch) < num:
            break
        start += len(batch)
    return items, f"{source['label']} {len(items)} 条"


def fallback_links(q: str, selected: list[str]) -> list[dict]:
    links = []
    if "ebay" in selected:
        links.append({"source": "eBay", "url": f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(q)}"})
    for k in selected:
        if k in SOURCES:
            s = SOURCES[k]
            links.append({
                "source": s["label"],
                "url": f"https://www.google.com/search?q={quote_plus(q + ' site:' + s['domain'])}",
            })
    return links


# ---------- API ----------
app = FastAPI(title=APP_TITLE)


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=2),
    scale: str = "",
    brand: str = "",
    sources: str = "ebay,yahoo_jp,mercari_jp,bunjang_kr,xianyu_cn",
    limit: int = 12,
    max_cny: Optional[float] = None,
):
    selected = [x.strip() for x in sources.split(",") if x.strip()]
    query = expand_query(q, scale, brand)
    rates, fx_source = get_fx_rates()
    results: list[Item] = []
    notes: list[str] = []

    if "ebay" in selected:
        try:
            r, note = search_ebay(query, limit, rates)
            results += r
            notes.append(note)
        except Exception as e:
            notes.append(f"eBay 错误：{type(e).__name__}")

    for key in selected:
        if key not in SOURCES:
            continue
        try:
            r, note = google_cse_search(query, key, limit, rates)
            results += r
            notes.append(note)
        except Exception as e:
            notes.append(f"{SOURCES[key]['label']} 错误：{type(e).__name__}")

    results = dedupe(results)
    if max_cny is not None:
        # Keep unknown-price results, but mark known bargains first.
        results.sort(key=lambda x: (x.total_cny is None, (x.total_cny or 10**12) > max_cny, x.total_cny or 10**12))
    else:
        results.sort(key=lambda x: (x.total_cny is None, x.total_cny or 10**12))

    return {
        "query": query,
        "fx_source": fx_source,
        "rates": rates,
        "count": len(results),
        "items": [asdict(x) for x in results],
        "notes": notes,
        "fallback_links": fallback_links(query, selected),
    }


@app.get("/api/health")
def health():
    return {"ok": True, "name": APP_TITLE}


HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>F1 赛车模型全球比价</title>
<style>
:root{--bg:#090b10;--panel:#11141b;--panel2:#171b24;--line:#2a3040;--text:#f6f7fb;--muted:#9da5b4;--accent:#ff334f;--green:#43d17b;--amber:#ffc857}
*{box-sizing:border-box} body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:radial-gradient(circle at 15% 0,#1b2130 0,#090b10 35%);color:var(--text)}
.wrap{max-width:1440px;margin:auto;padding:30px 22px 60px}.hero{display:flex;gap:24px;align-items:end;justify-content:space-between;margin-bottom:22px}.eyebrow{font-size:12px;letter-spacing:.16em;color:#ff8092;font-weight:800}.hero h1{font-size:34px;margin:6px 0}.hero p{margin:0;color:var(--muted);max-width:720px}.badge{border:1px solid var(--line);padding:9px 12px;border-radius:999px;color:#cbd1dc;background:#0f1218;white-space:nowrap}
.panel{background:linear-gradient(180deg,rgba(23,27,36,.96),rgba(14,17,23,.96));border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 20px 60px rgba(0,0,0,.25)}
.formgrid{display:grid;grid-template-columns:2fr .6fr .75fr .65fr;gap:12px}.field label{display:block;font-size:12px;color:var(--muted);margin:0 0 7px 2px}.field input,.field select{width:100%;border:1px solid #31394b;background:#0d1016;color:var(--text);border-radius:12px;padding:12px 13px;outline:none}.field input:focus,.field select:focus{border-color:#667189}
.sources{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}.chip{display:flex;align-items:center;gap:6px;background:#0d1016;border:1px solid #303747;border-radius:999px;padding:8px 10px;font-size:13px}.chip input{accent-color:var(--accent)}
.actions{display:flex;gap:10px;align-items:center}.btn{border:0;background:var(--accent);color:white;font-weight:800;border-radius:12px;padding:12px 20px;cursor:pointer}.btn:disabled{opacity:.5}.subtle{font-size:12px;color:var(--muted)}
.status{display:flex;justify-content:space-between;align-items:center;margin:20px 2px 10px}.status h2{font-size:16px;margin:0}.status .meta{color:var(--muted);font-size:12px}
.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:16px;background:#0e1117}table{width:100%;border-collapse:collapse;min-width:1020px}th{position:sticky;top:0;background:#141821;color:#aab2c0;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;padding:12px;border-bottom:1px solid var(--line)}td{padding:12px;border-bottom:1px solid #202633;vertical-align:middle}.src{font-weight:800;font-size:12px}.title{font-size:13px;font-weight:700;line-height:1.35}.title a{color:#f4f6fb;text-decoration:none}.title a:hover{text-decoration:underline}.thumb{width:62px;height:46px;object-fit:cover;border-radius:8px;background:#222}.muted{color:var(--muted);font-size:11px}.money{font-variant-numeric:tabular-nums;white-space:nowrap}.total{font-weight:900}.deal{color:var(--green)}.warn{color:var(--amber)}.pill{display:inline-block;border:1px solid #353d4e;border-radius:999px;padding:3px 7px;font-size:10px;color:#cbd1dc}
.links{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin-top:12px}.linkcard{border:1px solid var(--line);background:#0d1016;border-radius:12px;padding:10px;text-decoration:none;color:#dfe4ee;font-size:12px}.linkcard:hover{border-color:#566175}
.empty{text-align:center;color:var(--muted);padding:38px}.notes{margin-top:10px;color:#8e97a7;font-size:11px;line-height:1.6}
@media(max-width:900px){.formgrid{grid-template-columns:1fr 1fr}.hero{align-items:start;flex-direction:column}.hero h1{font-size:28px}}@media(max-width:560px){.formgrid{grid-template-columns:1fr}.wrap{padding:18px 12px 40px}}
</style>
</head>
<body><div class="wrap">
<div class="hero"><div><div class="eyebrow">F1 MODEL MARKET WATCH</div><h1>赛车模型全球检索 / 比价</h1><p>一次输入，聚合中古、拍卖与零售结果；自动换算人民币并优先显示低于你的目标价的商品。</p></div><div class="badge">MVP · 本地运行</div></div>
<div class="panel">
  <div class="formgrid">
    <div class="field"><label>搜索模型 / 车手 / 特别版</label><input id="q" value="Lewis Hamilton W11" placeholder="例如：维斯塔潘 RB16B 白牛 / Hamilton 7冠 特注" /></div>
    <div class="field"><label>比例</label><select id="scale"><option value="">全部</option><option>1:18</option><option>1:43</option><option>1:12</option><option>1:5</option><option>1:2</option></select></div>
    <div class="field"><label>品牌</label><select id="brand"><option value="all">全部</option><option>Spark</option><option>Minichamps</option><option>Looksmart</option><option>BBR</option><option>Amalgam</option><option>Autoart</option></select></div>
    <div class="field"><label>目标到手价 ≤ CNY</label><input id="max" type="number" value="3000" min="0" step="100" /></div>
  </div>
  <div class="sources" id="sources">
    <label class="chip"><input type="checkbox" value="ebay" checked> eBay</label>
    <label class="chip"><input type="checkbox" value="yahoo_jp" checked> Yahoo!拍卖</label>
    <label class="chip"><input type="checkbox" value="mercari_jp" checked> Mercari JP</label>
    <label class="chip"><input type="checkbox" value="rakuma_jp"> Rakuma</label>
    <label class="chip"><input type="checkbox" value="bunjang_kr" checked> Bunjang</label>
    <label class="chip"><input type="checkbox" value="joongna_kr"> Joongna</label>
    <label class="chip"><input type="checkbox" value="xianyu_cn" checked> 闲鱼</label>
    <label class="chip"><input type="checkbox" value="carousell"> Carousell</label>
  </div>
  <div class="actions"><button class="btn" id="go" onclick="searchNow()">开始比价</button><span class="subtle">没有 API Key 时仍会生成各平台一键检索入口。</span></div>
</div>
<div class="status"><h2 id="headline">结果</h2><div class="meta" id="meta">等待检索</div></div>
<div class="tablewrap"><table><thead><tr><th></th><th>平台</th><th>商品</th><th>标价</th><th>运费</th><th>估算到手价</th><th>状态</th></tr></thead><tbody id="rows"><tr><td class="empty" colspan="7">输入关键词后开始检索</td></tr></tbody></table></div>
<div id="linkTitle" class="status" style="display:none"><h2>平台检索入口</h2><div class="meta">用于 API 未配置或站点不提供公开商品 API 的渠道</div></div>
<div class="links" id="links"></div><div class="notes" id="notes"></div>
</div>
<script>
const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
function money(v,c){return v==null?'—':`${esc(c||'')} ${Number(v).toLocaleString(undefined,{maximumFractionDigits:2})}`}
function totalClass(v,max){if(v==null)return 'warn';return max && v<=max?'deal':''}
async function searchNow(){
 const q=document.getElementById('q').value.trim(); if(!q)return;
 const scale=document.getElementById('scale').value, brand=document.getElementById('brand').value;
 const max=document.getElementById('max').value;
 const sources=[...document.querySelectorAll('#sources input:checked')].map(x=>x.value).join(',');
 const btn=document.getElementById('go'); btn.disabled=true; btn.textContent='检索中…';
 document.getElementById('meta').textContent='正在检索多个渠道';
 try{
   const p=new URLSearchParams({q,scale,brand,sources,limit:'12'}); if(max)p.set('max_cny',max);
   const r=await fetch('/api/search?'+p.toString()); const d=await r.json();
   const maxNum=max?Number(max):null;
   document.getElementById('headline').textContent=`结果 · ${d.count} 条`;
   document.getElementById('meta').textContent=`查询：${d.query} · 汇率：${d.fx_source}`;
   const rows=document.getElementById('rows'); rows.innerHTML='';
   if(!d.items.length){ rows.innerHTML='<tr><td class="empty" colspan="7">当前 API 未返回结构化结果；请使用下方平台检索入口，或按 README 配置 API Key。</td></tr>'; }
   for(const x of d.items){
     const deal=maxNum && x.total_cny!=null && x.total_cny<=maxNum;
     const tr=document.createElement('tr');
     tr.innerHTML=`<td>${x.image?`<img class="thumb" src="${esc(x.image)}" loading="lazy" referrerpolicy="no-referrer">`:''}</td>
       <td><div class="src">${esc(x.source)}</div><div class="muted">${esc(x.price_confidence==='structured'?'结构化价格':x.price_confidence==='snippet'?'网页摘要估价':'价格未知')}</div></td>
       <td><div class="title"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a></div><div class="muted">${esc(x.condition||'')} ${esc(x.seller||'')}</div></td>
       <td class="money">${money(x.price,x.currency)}</td><td class="money">${money(x.shipping,x.shipping_currency||x.currency)}</td>
       <td class="money total ${totalClass(x.total_cny,maxNum)}">${x.total_cny==null?'—':'¥ '+Number(x.total_cny).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
       <td>${deal?'<span class="pill deal">低于目标价</span>':x.total_cny==null?'<span class="pill warn">需打开确认</span>':'<span class="pill">观察</span>'}</td>`;
     rows.appendChild(tr);
   }
   const links=document.getElementById('links'); links.innerHTML='';
   for(const x of d.fallback_links||[]){const a=document.createElement('a');a.className='linkcard';a.href=x.url;a.target='_blank';a.rel='noopener';a.innerHTML=`打开 ${esc(x.source)} 搜索 ↗`;links.appendChild(a)}
   document.getElementById('linkTitle').style.display=(d.fallback_links||[]).length?'flex':'none';
   document.getElementById('notes').textContent=(d.notes||[]).join(' · ');
 }catch(e){document.getElementById('rows').innerHTML=`<tr><td class="empty" colspan="7">请求失败：${esc(e.message)}</td></tr>`}
 finally{btn.disabled=false;btn.textContent='开始比价'}
}
</script></body></html>'''

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8765")))
