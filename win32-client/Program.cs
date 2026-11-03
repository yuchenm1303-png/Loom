using System.Diagnostics;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Loom.Win32Remote;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new LoomRemoteWindow());
    }
}

// This companion renders the exact Loom Web React app served by the gateway.
// It never claims to run the local Python Host, Browser Use or Computer Use.
internal sealed class LoomRemoteWindow : Form
{
    // The query marks this as a remote-only shell. No script/native bridge is
    // injected; Loom Web records the mode in sessionStorage across OAuth reloads.
    private const string LoomUrl = "https://loom.smirel.com/?loom_client=win32-remote";
    private const string LoomWebUrl = "https://loom.smirel.com/";
    private const string WebView2InstallUrl = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/";
    private readonly WebView2 _web = new() { Dock = DockStyle.Fill };
    private readonly Panel _errorPanel = new() { Dock = DockStyle.Fill, Visible = false };
    private CoreWebView2Environment? _environment;

    public LoomRemoteWindow()
    {
        Text = "Loom Remote · Windows 32-bit";
        Width = 1200;
        Height = 800;
        MinimumSize = new Size(700, 480);
        BackColor = Color.FromArgb(26, 27, 30);
        ForeColor = Color.White;

        var toolbar = new ToolStrip
        {
            Dock = DockStyle.Top,
            GripStyle = ToolStripGripStyle.Hidden,
            BackColor = Color.FromArgb(32, 33, 37),
            ForeColor = Color.White,
            Renderer = new ToolStripProfessionalRenderer(),
        };
        toolbar.Items.Add(MakeButton("Loom", () => NavigateHome()));
        toolbar.Items.Add(MakeButton("刷新", () => _web.CoreWebView2?.Reload()));
        toolbar.Items.Add(MakeButton("在浏览器中打开", () => OpenInBrowser(LoomWebUrl)));
        toolbar.Items.Add(new ToolStripLabel("  ·  32 位远程客户端（需连接在线 Loom Host）")
        {
            ForeColor = Color.Gainsboro,
        });

        var errorText = new Label
        {
            Dock = DockStyle.Top,
            Height = 130,
            TextAlign = ContentAlignment.MiddleCenter,
            ForeColor = Color.White,
            Text = "无法加载 Loom Web。请检查网络连接和 Microsoft Edge WebView2 Runtime。",
        };
        var retryButton = new Button { Text = "重试", Width = 120, Height = 38, Top = 150, Left = 35 };
        retryButton.Click += async (_, _) => await InitializeAsync();
        var runtimeButton = new Button { Text = "安装 WebView2", Width = 145, Height = 38, Top = 150, Left = 170 };
        runtimeButton.Click += (_, _) => OpenInBrowser(WebView2InstallUrl);
        _errorPanel.Controls.Add(errorText);
        _errorPanel.Controls.Add(retryButton);
        _errorPanel.Controls.Add(runtimeButton);

        Controls.Add(_web);
        Controls.Add(_errorPanel);
        Controls.Add(toolbar);
        Shown += async (_, _) => await InitializeAsync();
    }

    private static ToolStripButton MakeButton(string title, Action onClick)
    {
        var button = new ToolStripButton(title) { ForeColor = Color.White, DisplayStyle = ToolStripItemDisplayStyle.Text };
        button.Click += (_, _) => onClick();
        return button;
    }

    private async Task InitializeAsync()
    {
        try
        {
            // Stable per-user profile keeps secure HttpOnly login cookies between launches.
            var userData = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Smirel", "LoomWin32Remote", "WebView2");
            Directory.CreateDirectory(userData);
            _environment ??= await CoreWebView2Environment.CreateAsync(userDataFolder: userData);

            if (_web.CoreWebView2 is null)
            {
                await _web.EnsureCoreWebView2Async(_environment);
                ConfigureWebView(_web);
            }
            _errorPanel.Visible = false;
            NavigateHome();
        }
        catch (Exception error)
        {
            Debug.WriteLine(error);
            _errorPanel.Visible = true;
            _errorPanel.BringToFront();
        }
    }

    private void ConfigureWebView(WebView2 browser)
    {
        var core = browser.CoreWebView2;
        core.Settings.AreHostObjectsAllowed = false;
        core.Settings.IsWebMessageEnabled = false;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsScriptEnabled = true;
        core.Settings.AreDefaultContextMenusEnabled = true;

        // No native bridge is injected: all operations retain Loom Web's
        // authenticated WSS relay and explicit remote-device permission checks.
        core.NavigationStarting += (_, args) =>
        {
            if (!IsSafeNavigation(args.Uri)) args.Cancel = true;
        };
        core.NewWindowRequested += async (_, args) => await OpenPopupAsync(args);
        core.PermissionRequested += (_, args) => args.State = CoreWebView2PermissionState.Deny;
        browser.NavigationCompleted += (_, args) =>
        {
            if (!args.IsSuccess && args.WebErrorStatus != CoreWebView2WebErrorStatus.OperationCanceled)
            {
                _errorPanel.Visible = true;
                _errorPanel.BringToFront();
            }
        };
    }

    private async Task OpenPopupAsync(CoreWebView2NewWindowRequestedEventArgs args)
    {
        // OAuth uses popups and must share the parent WebView2 cookie profile.
        // Reject non-HTTPS popup origins; no shell commands or local files.
        if (!IsSafeNavigation(args.Uri) || _environment is null)
        {
            args.Handled = true;
            return;
        }

        var deferral = args.GetDeferral();
        Form? popup = null;
        try
        {
            popup = new Form
            {
                Text = "Loom · 登录",
                Width = 650,
                Height = 760,
                StartPosition = FormStartPosition.CenterParent,
                BackColor = Color.FromArgb(26, 27, 30),
            };
            var child = new WebView2 { Dock = DockStyle.Fill };
            popup.Controls.Add(child);
            await child.EnsureCoreWebView2Async(_environment);
            ConfigureWebView(child);
            child.CoreWebView2.WindowCloseRequested += (_, _) => popup.Close();
            args.NewWindow = child.CoreWebView2;
            args.Handled = true;
            popup.Show(this);
        }
        catch (Exception error)
        {
            Debug.WriteLine(error);
            args.Handled = true;
            popup?.Dispose();
        }
        finally
        {
            deferral.Complete();
        }
    }

    private void NavigateHome()
    {
        if (_web.CoreWebView2 is not null) _web.CoreWebView2.Navigate(LoomUrl);
    }

    private static bool IsSafeNavigation(string? url) =>
        Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && uri.Scheme == Uri.UriSchemeHttps
        && string.IsNullOrEmpty(uri.UserInfo);

    private static void OpenInBrowser(string url)
    {
        if (!IsSafeNavigation(url)) return;
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch (Exception error) { Debug.WriteLine(error); }
    }

    protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
    {
        if (keyData == Keys.F5)
        {
            _web.CoreWebView2?.Reload();
            return true;
        }
        return base.ProcessCmdKey(ref msg, keyData);
    }
}
