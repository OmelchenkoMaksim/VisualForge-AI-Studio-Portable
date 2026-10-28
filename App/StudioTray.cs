// Windows notification area UI; all native and GDI resources belong to this host.
using System;
using System.Collections;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace VisualForge {
    public sealed class StudioTray : IDisposable {
        [DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr icon);
        private readonly IDictionary state;
        private readonly Icon[] frames;
        private readonly Timer timer;
        private readonly ApplicationContext context;
        private int frame;
        private bool? lastBusy;
        private bool disposed;
        public readonly NotifyIcon Notification;
        public readonly ContextMenuStrip Menu;
        public int Frame { get { return frame; } }

        public StudioTray(IDictionary sharedState, string imagePath) {
            state = sharedState;
            frames = new Icon[24];
            context = new ApplicationContext();
            try {
                Size iconSize = SystemInformation.SmallIconSize;
                using (Image source = Image.FromFile(imagePath)) {
                    for (int i = 0; i < frames.Length; i++) {
                        using (Bitmap bitmap = new Bitmap(iconSize.Width, iconSize.Height)) {
                            using (Graphics g = Graphics.FromImage(bitmap)) {
                                g.Clear(Color.Transparent);
                                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                                g.SmoothingMode = SmoothingMode.AntiAlias;
                                g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                                // Extra padding made the logo tiny after the shell scaled the old 48px canvas down.
                                g.TranslateTransform(iconSize.Width / 2f, iconSize.Height / 2f);
                                g.RotateTransform(i * 360f / frames.Length);
                                g.DrawImage(source, -iconSize.Width / 2f, -iconSize.Height / 2f, iconSize.Width, iconSize.Height);
                            }
                            IntPtr handle = bitmap.GetHicon();
                            try { using (Icon borrowed = Icon.FromHandle(handle)) frames[i] = (Icon)borrowed.Clone(); }
                            finally { DestroyIcon(handle); }
                        }
                    }
                }
                // The shared ICO contains dedicated small sizes, avoiding another resize of the splash PNG at idle.
                string iconPath = System.IO.Path.ChangeExtension(imagePath, ".ico");
                if (System.IO.File.Exists(iconPath)) {
                    using (Icon idle = new Icon(iconPath, iconSize)) {
                        frames[0].Dispose();
                        frames[0] = (Icon)idle.Clone();
                    }
                }
                Menu = new ContextMenuStrip();
                Menu.Items.Add("Открыть приложение на весь экран", null, delegate { state["Open"] = true; });
                Menu.Items.Add("Закрыть приложение полностью", null, delegate { state["Quit"] = true; });
                Notification = new NotifyIcon();
                Notification.Icon = frames[0];
                Notification.Text = "VisualForge AI Studio";
                Notification.ContextMenuStrip = Menu;
                Notification.DoubleClick += delegate { state["Open"] = true; };
                Notification.Visible = true;
                timer = new Timer();
                timer.Interval = 90;
                timer.Tick += delegate { Update(); };
                timer.Start();
                state["Shown"] = true;
            } catch { Dispose(); throw; }
        }
        public void Update() {
            if (disposed) return;
            if ((bool)state["Close"]) { context.ExitThread(); return; }
            bool busy = (bool)state["Busy"];
            if (!busy && lastBusy == false) return;
            lastBusy = busy;
            frame = busy ? (frame + 1) % frames.Length : 0;
            Notification.Icon = frames[frame];
            Notification.Text = busy ? "VisualForge AI Studio · генерация" : "VisualForge AI Studio";
        }
        public void Run() { Application.Run(context); }
        public void Dispose() {
            if (disposed) return;
            disposed = true;
            state["Shown"] = false;
            if (timer != null) { timer.Stop(); timer.Dispose(); }
            if (Notification != null) { Notification.Visible = false; Notification.Dispose(); }
            if (Menu != null) Menu.Dispose();
            foreach (Icon icon in frames) if (icon != null) icon.Dispose();
            if (context != null) context.Dispose();
        }
    }
}
