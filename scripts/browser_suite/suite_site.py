"""The local web app behind the live browser suite: nine pages, each reporting what was done to it.

Every page posts the facts of a finished task to /api/result/<task>; the suite checks those, never the
model's own account. Served on 127.0.0.1 only, from memory, by `serve()`.
"""
from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

STYLE = """<style>
body { font: 15px "Segoe UI", sans-serif; margin: 24px; color: #222; max-width: 760px; }
label { display: block; margin: 10px 0 4px; } input, select { font: inherit; padding: 4px 6px; }
button { font: inherit; padding: 5px 12px; margin: 6px 4px 6px 0; cursor: pointer; }
table { border-collapse: collapse; width: 100%; } td, th { border: 1px solid #ccc; padding: 4px 8px; text-align: left; }
.done { color: #1a7f37; font-weight: 600; } .error { color: #b00020; }
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: none; align-items: center; justify-content: center; }
.modal > div { background: white; padding: 20px; border-radius: 8px; min-width: 300px; }
.combo { position: relative; display: inline-block; } .combo-list { position: absolute; background: white; border: 1px solid #999; display: none; z-index: 2; }
.combo-list div { padding: 4px 10px; cursor: pointer; } .combo-list div:hover { background: #eef; }
</style>"""

REPORT = """<script>
async function report(task, data) {
  await fetch('/api/result/' + task, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(data)});
}
async function saved(task) {
  const response = await fetch('/api/result/' + task);
  return response.ok ? response.json() : {};
}
</script>"""


def page(title, body):
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{title}</title>{STYLE}{REPORT}</head><body>{body}</body></html>"


CATEGORIES = ["Garden", "Kitchen", "Office", "Toys"]


def products(seed):
    rng = random.Random(seed)
    names = ["Lamp", "Shovel", "Mug", "Stapler", "Kite", "Hose", "Kettle", "Folder", "Puzzle", "Rake", "Pan",
             "Marker", "Robot", "Planter", "Whisk", "Binder", "Yo-yo", "Gloves", "Tray", "Clip"]
    rows = []
    for i in range(60):
        rows.append({"id": 100 + i, "name": f"{rng.choice(names)} {chr(65 + i % 26)}{i}", "category": CATEGORIES[i % 4],
                     "price": round(rng.uniform(4, 90), 2)})
    return rows


ORDERS = [
    ("1001", "Ana Ruiz", "2026-10-02", 42.50), ("1002", "Bob Ng", "2026-10-03", 18.25),
    ("1003", "Bob Ng", "2026-09-28", 99.00), ("1004", "Chen Li", "2026-10-11", 12.00),
    ("1005", "Bob Ng", "2026-10-15", 31.10), ("1006", "Ana Ruiz", "2026-10-21", 7.75),
    ("1007", "Bob Ng", "2026-11-01", 55.00), ("1008", "Dee Park", "2026-10-30", 23.40),
    ("1009", "Bob Ng", "2026-10-29", 9.65), ("1010", "Chen Li", "2026-10-05", 61.20),
]


class Site:
    def __init__(self, seed: int = 7):
        self.results: dict[str, dict] = {}
        self.seed = seed
        self.items = products(seed)
        key_source = random.Random(seed + 1)
        self.api_key = "LK-" + "".join(key_source.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(8))

    def cheapest(self, category):
        return min((p for p in self.items if p["category"] == category), key=lambda p: p["price"])

    def expected(self):
        bob_october = round(sum(t for _id, who, day, t in ORDERS if who == "Bob Ng" and day.startswith("2026-10")), 2)
        return {
            "login": {"notifications": True, "user": "tester"},
            "shop": {"item_id": self.cheapest("Garden")["id"], "name": "Ada Lovelace", "shipping": "express"},
            "wizard": {"email": "grace@example.com", "plan": "Team", "agreed": True},
            "feed": {"selected": "Item 73"},
            "newtab": {"api_key": self.api_key},
            "iframe": {"holder": "Grace Hopper", "amount": "25"},
            "xframe": {"holder": "Alan Turing", "amount": "40"},
            "extract": {"answer": f"{bob_october:.2f}"},
            "widget": {"quantity": "4"},
            "modal": {"deleted": "Old notes", "remaining": 3},
        }

    # -- pages ----------------------------------------------------------------

    def render(self, path):
        if path == "/login":
            return page("Sign in", """<h2>Sign in</h2>
<label for=u>Username</label><input id=u autocomplete=off>
<label for=p>Password</label><input id=p type=password>
<div><button id=go>Sign in</button></div><div id=msg class=error></div>
<script>
document.getElementById('go').onclick = () => {
  const u = document.getElementById('u').value, p = document.getElementById('p').value;
  if (u === 'tester' && p === 'orange-42') { sessionStorage.setItem('user', u); location.href = '/account'; }
  else document.getElementById('msg').textContent = 'Wrong username or password.';
};
</script>""")
        if path == "/account":
            return page("Account", """<h2>Account settings</h2><p id=who></p>
<label><input id=n type=checkbox> Email notifications</label>
<label><input id=s type=checkbox checked> Show tips</label>
<button id=save>Save settings</button><div id=done class=done></div>
<script>
const user = sessionStorage.getItem('user');
if (!user) location.href = '/login';
document.getElementById('who').textContent = 'Signed in as ' + user;
saved('login').then(s => { if ('notifications' in s) { document.getElementById('n').checked = s.notifications;
  document.getElementById('s').checked = s.tips; } });
document.getElementById('save').onclick = async () => {
  await report('login', {user, notifications: document.getElementById('n').checked, tips: document.getElementById('s').checked});
  document.getElementById('done').textContent = 'Settings saved';
};
</script>""")
        if path == "/shop":
            data = json.dumps(self.items)
            return page("Loom Test Shop", f"""<h2>Shop</h2>
<label for=cat>Category</label><select id=cat><option value=''>All</option>{''.join(f'<option>{c}</option>' for c in CATEGORIES)}</select>
<button id=sort>Sort by price</button>
<table><thead><tr><th>Item</th><th>Category</th><th>Price</th><th></th></tr></thead><tbody id=rows></tbody></table>
<div><button id=prev>Previous</button><span id=pg></span><button id=next>Next</button></div>
<p>Cart: <span id=cart>empty</span></p><button id=checkout>Checkout</button>
<div id=co style='display:none'><h3>Checkout</h3><label for=name>Full name</label><input id=name>
<label for=ship>Shipping</label><select id=ship><option value=standard>Standard</option><option value=express>Express</option></select>
<button id=place>Place order</button></div><div id=done class=done></div>
<script>
const items = {data}; let page = 0, sorted = false, cart = null;
function view() {{
  const cat = document.getElementById('cat').value;
  let list = items.filter(i => !cat || i.category === cat);
  if (sorted) list = [...list].sort((a, b) => a.price - b.price);
  const pages = Math.max(1, Math.ceil(list.length / 10)); page = Math.min(page, pages - 1);
  document.getElementById('rows').innerHTML = list.slice(page * 10, page * 10 + 10).map(i =>
    `<tr><td>${{i.name}}</td><td>${{i.category}}</td><td>$${{i.price.toFixed(2)}}</td><td><button data-id=${{i.id}}>Add to cart</button></td></tr>`).join('');
  document.getElementById('pg').textContent = ` Page ${{page + 1}} of ${{pages}} `;
  document.querySelectorAll('[data-id]').forEach(b => b.onclick = () => {{
    cart = items.find(i => i.id == b.dataset.id); document.getElementById('cart').textContent = cart.name; }});
}}
document.getElementById('cat').onchange = () => {{ page = 0; view(); }};
document.getElementById('sort').onclick = () => {{ sorted = true; view(); }};
document.getElementById('prev').onclick = () => {{ page = Math.max(0, page - 1); view(); }};
document.getElementById('next').onclick = () => {{ page += 1; view(); }};
document.getElementById('checkout').onclick = () => {{ if (cart) document.getElementById('co').style.display = 'block'; }};
document.getElementById('place').onclick = async () => {{
  await report('shop', {{item_id: cart && cart.id, name: document.getElementById('name').value, shipping: document.getElementById('ship').value}});
  document.getElementById('done').textContent = 'Order placed';
}};
view();
</script>""")
        if path == "/wizard":
            return page("Sign-up Wizard", """<h2>Sign up</h2>
<div id=s1><h3>Step 1 of 3</h3><label for=email>Email</label><input id=email><div id=err class=error></div><button id=n1>Next</button></div>
<div id=s2 style='display:none'><h3>Step 2 of 3</h3><p>Plan</p>
<div class=combo><button id=planbtn>Choose a plan</button><div class=combo-list id=planlist>
<div data-v=Starter>Starter</div><div data-v=Team>Team</div><div data-v=Enterprise>Enterprise</div></div></div>
<button id=n2>Next</button><div id=err2 class=error></div></div>
<div id=s3 style='display:none'><h3>Step 3 of 3</h3><label><input id=agree type=checkbox> I agree to the terms</label>
<button id=finish>Create account</button><div id=err3 class=error></div></div><div id=done class=done></div>
<script>
let plan = '';
const show = n => ['s1','s2','s3'].forEach((id, i) => document.getElementById(id).style.display = i === n ? 'block' : 'none');
document.getElementById('n1').onclick = () => {
  const v = document.getElementById('email').value.trim();
  if (!/^[^@\\s]+@[^@\\s]+\\.[a-z]{2,}$/i.test(v)) { document.getElementById('err').textContent = 'Please enter a valid email address.'; return; }
  show(1);
};
document.getElementById('planbtn').onclick = () => { const l = document.getElementById('planlist'); l.style.display = l.style.display === 'block' ? 'none' : 'block'; };
document.querySelectorAll('#planlist div').forEach(d => d.onclick = () => {
  plan = d.dataset.v; document.getElementById('planbtn').textContent = plan; document.getElementById('planlist').style.display = 'none'; });
document.getElementById('n2').onclick = () => { if (!plan) { document.getElementById('err2').textContent = 'Choose a plan.'; return; } show(2); };
document.getElementById('finish').onclick = async () => {
  const agreed = document.getElementById('agree').checked;
  if (!agreed) { document.getElementById('err3').textContent = 'You must agree to continue.'; return; }
  await report('wizard', {email: document.getElementById('email').value.trim(), plan, agreed});
  document.getElementById('done').textContent = 'Account created';
};
</script>""")
        if path == "/feed":
            return page("Item Feed", """<h2>Item feed</h2><div id=list><p id=loading>Loading...</p></div><div id=done class=done></div>
<script>
let shown = 0;
function more(n) {
  const list = document.getElementById('list');
  for (let i = 0; i < n; i++) { shown++; const row = document.createElement('div');
    row.innerHTML = `<p>Item ${shown} <button>Select</button></p>`;
    const id = shown; row.querySelector('button').onclick = async () => {
      await report('feed', {selected: 'Item ' + id}); row.querySelector('button').textContent = 'Selected';
      document.getElementById('done').textContent = 'Selected Item ' + id; };
    list.appendChild(row); }
}
setTimeout(() => { document.getElementById('loading').remove(); more(20); }, 1200);
window.addEventListener('scroll', () => {
  if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 50 && shown < 120) setTimeout(() => more(20), 400);
});
</script>""")
        if path == "/docs":
            return page("API Docs", """<h2>API docs</h2><p>Paste your key from the developer page to enable examples.</p>
<p><a href='/developer' target='_blank'>Open the developer page</a></p>
<label for=key>API key</label><input id=key><button id=save>Save key</button><div id=done class=done></div>
<script>
document.getElementById('save').onclick = async () => {
  await report('newtab', {api_key: document.getElementById('key').value.trim()}); document.getElementById('done').textContent = 'Key saved'; };
saved('newtab').then(s => { if (s.api_key) document.getElementById('done').textContent = 'Saved key: ' + s.api_key.slice(0, 3) + '...' + s.api_key.slice(-2); });
</script>""")
        if path == "/developer":
            return page("Developer", f"<h2>Developer page</h2><p>Your API key is <code id=k>{self.api_key}</code></p>")
        if path == "/embed":
            return page("Checkout", """<h2>Checkout</h2><p>Pay with the secure form below.</p>
<iframe src='/payframe' style='width:420px;height:220px;border:1px solid #888'></iframe>""")
        if path == "/embed-cross":
            # Payment frames are usually another origin: localhost is not 127.0.0.1.
            return page("Checkout", f"""<h2>Checkout</h2><p>Pay with the secure form below.</p>
<iframe src='http://localhost:{self.port}/payframe?task=xframe' style='width:420px;height:220px;border:1px solid #888'></iframe>""")
        if path == "/payframe":
            return page("Payment", """<label for=h>Card holder</label><input id=h><label for=a>Amount</label><input id=a type=number>
<button id=pay>Pay</button><div id=done class=done></div>
<script>
document.getElementById('pay').onclick = async () => {
  await report(new URLSearchParams(location.search).get('task') || 'iframe', {holder: document.getElementById('h').value.trim(), amount: document.getElementById('a').value});
  document.getElementById('done').textContent = 'Payment sent'; };
</script>""")
        if path == "/orders":
            rows = "".join(f"<tr><td>{i}</td><td>{who}</td><td>{day}</td><td>${total:.2f}</td></tr>" for i, who, day, total in ORDERS)
            return page("Orders", f"<h2>Orders</h2><table><thead><tr><th>Order</th><th>Customer</th><th>Date</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table>")
        if path == "/widget":
            return page("Quantity Widget", """<h2>Cart item</h2><qty-picker></qty-picker><div id=done class=done></div>
<script>
customElements.define('qty-picker', class extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = `<label>Quantity <input id=q type=number value=1 min=1 max=9></label> <button id=apply>Apply</button>`;
    root.getElementById('apply').onclick = async () => {
      await report('widget', {quantity: root.getElementById('q').value}); document.getElementById('done').textContent = 'Quantity applied'; };
    saved('widget').then(s => { if (s.quantity) root.getElementById('q').value = s.quantity; });
  }
});
</script>""")
        if path == "/drafts":
            return page("Drafts", """<h2>Drafts</h2><ul id=list></ul><div id=done class=done></div>
<div class=modal id=modal><div><p id=question></p><button id=keep>Cancel</button><button id=confirm>Delete</button></div></div>
<script>
let drafts = ['Trip plan', 'Old notes', 'Budget 2027', 'Gift ideas'], pending = null;
function view() { document.getElementById('list').innerHTML = drafts.map(d => `<li>${d} <button data-d="${d}">Delete</button></li>`).join('');
  document.querySelectorAll('[data-d]').forEach(b => b.onclick = () => { pending = b.dataset.d;
    document.getElementById('question').textContent = `Delete the draft "${pending}"? This cannot be undone.`;
    document.getElementById('modal').style.display = 'flex'; }); }
document.getElementById('keep').onclick = () => { document.getElementById('modal').style.display = 'none'; pending = null; };
document.getElementById('confirm').onclick = async () => {
  drafts = drafts.filter(d => d !== pending); document.getElementById('modal').style.display = 'none';
  await report('modal', {deleted: pending, remaining: drafts.length}); document.getElementById('done').textContent = 'Deleted ' + pending; view(); };
saved('modal').then(s => { if (s.deleted) { drafts = drafts.filter(d => d !== s.deleted); } view(); });
</script>""")
        return None


def serve(site: Site) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/result/"):
                data = json.dumps(site.results.get(path.rsplit("/", 1)[-1], {})).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(data)
                return
            body = site.render(path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/result/"):
                length = int(self.headers.get("content-length") or 0)
                site.results[path.rsplit("/", 1)[-1]] = json.loads(self.rfile.read(length) or b"{}")
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    site.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
