"""单元测试：验证多用户专属连接弹窗与独立免密页面"""
import html
import portal

# 构造模拟元数据和用户数据
meta = {
    "public_ip": "1.2.3.4",
    "server_name": "hy2.example.com",
    "listen_port": 19984,
    "auth_password": "master_password",
    "obfs_password": "obfs_secret",
    "is_insecure": False,
    "subscription_port": 8443
}

users = {
    "test_user_1": {
        "password": "pwd_test_user_1",
        "expires_at": 2085974400,
        "ip_limit": 2,
        "limit_bytes": 10 * 1024**3,
        "used_bytes": 1024**2 * 500,
        "status": "active",
        "created_at": 1700000000,
        "note": "VIP 测试用户"
    }
}

token = "abcdef0123456789"
session_secret = "secret_key_for_test"

# 1. 验证 page_html 渲染
uri, clash, sing = portal.artifacts(meta)
admin_page = portal.page_html(meta, uri, "https://sub.link", clash, sing, users=users, token=token, session_secret=session_secret)

assert 'btn-user-connect' in admin_page, "用户行缺少专属连接按钮"
assert 'data-uid="test_user_1"' in admin_page, "专属连接按钮缺少 data-uid"
assert 'user-modal' in admin_page, "缺少专属连接模态框结构"
assert 'um-share-url' in admin_page, "模态框缺少专属页面复制框"
print("[✓] 管理后台多用户列表与专属连接弹窗结构渲染正常")

# 2. 验证 user_view_key
k = portal.user_view_key(session_secret, "test_user_1")
assert len(k) == 16, "user_view_key 长度错误"
print(f"[✓] 专属访问 Key 生成正常: {k}")

# 3. 验证独立个人连接页面 user_page_html
u_uri, u_clash, u_sing = portal.artifacts(meta, auth_override=users["test_user_1"]["password"], name_override="Hy2-test_user_1")
user_page = portal.user_page_html("hy2.example.com", "hy2.example.com", 19984, "Salamander", "test_user_1", users["test_user_1"], u_uri, u_clash, u_sing, "<svg>qr</svg>", token, k)

assert "个人专属连接" in user_page
assert "test_user_1" in user_page
assert "VIP 测试用户" in user_page
assert "u-hy2-uri" in user_page
assert html.escape(u_uri) in user_page
assert "u-clash-sub" in user_page
assert f"clash.yaml?k={k}" in user_page
assert "clash-test_user_1.yaml" in user_page
print("[✓] 独立用户个人专属页面渲染正常")

print("\nALL TEST CASES PASSED!")
