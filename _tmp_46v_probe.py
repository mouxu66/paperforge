import base64, os, time
import requests

key = os.environ["PAPERFORGE_GLM_VISION_API_KEY"]
img = base64.b64encode(open("uploads/figures/1512.03385/p1_v0.png", "rb").read()).decode()
h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
p = {
    "model": "glm-4.6v-flash",
    "thinking": {"type": "disabled"},
    "messages": [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
            {"type": "text", "text": "describe the x and y axis"}]}
    ],
    "max_tokens": 128,
}
for i in range(1, 9):
    t0 = time.perf_counter()
    r = requests.post("https://open.bigmodel.cn/api/paas/v4/chat/completions", headers=h, json=p, timeout=60)
    dt = time.perf_counter() - t0
    msg = r.text[:90].replace("\n", " ") if r.status_code != 200 else "OK"
    print(f"t={i*5-5}s -> status={r.status_code} lat={dt:.2f}s {msg}")
    time.sleep(5)
