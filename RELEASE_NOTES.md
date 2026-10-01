# Course YouTube Backup v1.0.1

修复 Google OAuth 自动打开浏览器失败时无法继续授权的问题。

- 授权链接就绪后，页面始终显示「打开 Google 授权页面」和「复制授权链接」。
- 自动打开失败或抛出异常时显示具体提示，仍可手动完成授权。
- 缺少或无效的 client_secret.json 立即显示错误，不再误报浏览器已打开。
- 成功、拒绝、超时后清除临时链接，保留原有账号 token。
- 实际验证真实本地 OAuth 回调和 Chrome 页面操作；Google 授权与 token 响应采用合成数据。

主程序仍只在 Mac 本地运行，所有上传固定为 private。
