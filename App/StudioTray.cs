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
        private readonly Icon idleIcon;
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
            frames = new Icon[72];
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
                // Keep the size-optimized idle ICO out of the PNG rotation frames: substituting it at frame zero
                // changes the logo's scale/alpha once per turn and makes an otherwise continuous loop jump.
                string iconPath = System.IO.Path.ChangeExtension(imagePath, ".ico");
                if (System.IO.File.Exists(iconPath)) {
                    using (Icon idle = new Icon(iconPath, iconSize)) {
                        idleIcon = (Icon)idle.Clone();
                    }
                }
                Menu = new ContextMenuStrip();
                Menu.Items.Add("Открыть приложение на весь экран", null, delegate { state["Open"] = true; });
                Menu.Items.Add("Закрыть приложение полностью", null, delegate { state["Quit"] = true; });
                Notification = new NotifyIcon();
                Notification.Icon = idleIcon ?? frames[0];
                Notification.Text = "VisualForge AI Studio";
                Notification.ContextMenuStrip = Menu;
                Notification.DoubleClick += delegate { state["Open"] = true; };
                Notification.Visible = true;
                timer = new Timer();
                // Smaller angular steps keep the same 2.16-second turn without the old 15-degree stepping.
                timer.Interval = 30;
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
            if (lastBusy != busy)
                Notification.Text = busy ? "VisualForge AI Studio · генерация" : "VisualForge AI Studio";
            lastBusy = busy;
            frame = busy ? (frame + 1) % frames.Length : 0;
            Notification.Icon = busy ? frames[frame] : (idleIcon ?? frames[0]);
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
            if (idleIcon != null) idleIcon.Dispose();
            if (context != null) context.Dispose();
        }
    }
}
