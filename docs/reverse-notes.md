# 反向工程笔记

## JSONP 响应格式

所有 `prehandle` 响应都是 JSONP：`_aq_000001({...})`。调用 `json.loads` 之前要用正则剥掉回调壳层，`client.py::parse_jsonp` 已封装。

## PoW（工作量证明）

```
找到 nonce 使得：md5(prefix + str(nonce)).hexdigest() == target_md5
```

平均 nonce 约 347，极大值约 1823，计算耗时 < 5 ms。

### `calc_time` 与风控

Python `hashlib` 对典型 20 位前缀通常在 ~200 ms 内完成，但真实 Chrome + TDC.js 2.0 报告的 `calc_time` 一般在 300–500 ms。如果上报一个异常低的 `calc_time`，会被风控标记。

`pow.solve_pow(prefix, target_md5, min_ms=300, max_ms=500)` 会在真实耗时不足 `min_ms` 时补足 sleep，并在 `[min_ms, max_ms]` 区间内随机化最终上报时间，模拟人工 / 浏览器的波动。所有 pipeline 默认用 `min_ms=300, max_ms=500`。

## TDC.js（`__TENCENT_CHAOS_VM`）

- 自定义字节码 VM，代码高度混淆
- 采集内容：设备指纹、Canvas / WebGL / Audio 指纹、行为轨迹
- 调用链：`TDC.setData(slideData)` → `TDC.getData()` → `TDC.getInfo()`
- jsdom 绕过要点：`pretendToBeVisual: true`、`runScripts: "dangerously"`
- 必须 patch 的字段：`screen`、`innerWidth / innerHeight`、`devicePixelRatio`、`navigator.webdriver`

## 轨迹格式

TDC 期望的输入是 `{elapsed_ms: "x,y"}` 字典。带 ±1 像素抖动的三阶缓入缓出（ease-in-out cubic）可以稳定通过检测。

对点击类（icon_click / word_click / image_select），多段点击轨迹之间需要用 `trajectory.merge_trajectories` 合并，保证 `kind="click"` 和时间戳连续性。

## `entry_url` 的用途

`solve(..., entry_url="https://your-site/login")` 会让 `client.py` 在 prehandle / getcapbysig / verify 三个请求上附带一致的 `Referer` 和 `Origin`。这两个 header 缺失时，请求仍然能通，但风控会更严格，并可能拒绝看起来"独立运行"的验证码请求。在真实业务站点集成时强烈建议传入。

## TCaptcha 2.0 的关键分类信号

`captcha_type.classify(dyn_show_info)` 根据以下规则定位类型（规则顺序不能换）：

1. `show_type == "click_image_uncheck"` → `image_select`
2. `bg_elem_cfg.click_cfg.data_type` 含 `"DynAnswerType_UC"` → `image_select`
3. 含 `fg_binding_list` → `slide`
4. `data_type` 含 `"DynAnswerType_POS"` + `instruction` 以 `"请依次点击"` 开头 + **没有** `fg_elem_list` → `word_click`
5. 同样条件但 **有** `fg_elem_list` → `icon_click`

`word_click` 必须在 `icon_click` 之前判定，否则会被误分。

## 请求字段差异

| Pipeline | `ans.type` | `ans.data` | `ans.elem_id` |
|---|---|---|---|
| `slide` | `DynAnswerType_POS` | `"x,y"` | `1` |
| `icon_click` / `word_click` | `DynAnswerType_POS` | `"x,y"` | `1..N`（按提示顺序） |
| `image_select` | `DynAnswerType_UC` | `"<region_id>"`（字符串） | 空串 `""` |

## LLM vision 的反向观察

- 服务端对点击坐标的容忍度：bbox 中心点通常足够，偏差 10–15 px 都能通过；但 word_click 的点击顺序严格按 `instruction` 汉字顺序
- 图像侧把候选区域画红框 + 编号显著提升 vision LLM 的稳定性，推荐保留这种"标注后喂图"的做法
- `temperature=0` + `max_tokens=64~128` + 明确要求"仅返回 JSON"能让主流 vision 模型（GPT-4o 系列、claude sonnet 系列、gemini-pro-vision 等）输出稳定
- 如果中继服务在 5xx / 超时时抛异常，`locate_chars` / `match_region` 各自有一次内部重试；业务层额外的重试通过 `max_retries` 控制整轮 prehandle→verify

## 已知检测向量

- `navigator.webdriver` —— 已在 jsdom patch 为 `false`
- Canvas 指纹 —— jsdom 返回空白，目前仍能通过（腾讯侧未强校验）
- 请求频率 —— 大规模场景用代理池，通过 `TCAPTCHA_PROXY` 或 `solve()` 的 proxy 参数配置
- TLS 指纹 —— 普通 Python HTTP 库会被 403；`client.py` 使用 `wreq.blocking.Client`（`Emulation.Chrome137`，可通过 `TCAPTCHA_EMULATION` 切换）做 Chrome TLS / HTTP2 指纹模拟
