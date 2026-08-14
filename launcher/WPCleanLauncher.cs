using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class WPCleanLauncher
{
    [STAThread]
    private static void Main()
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string script = Path.Combine(root, "giaodien.ps1");

            if (!File.Exists(script))
            {
                MessageBox.Show(
                    "Không tìm thấy giaodien.ps1 cạnh WP-Clean-Rebuild.exe.\n\nHãy đặt file EXE trong thư mục gốc của WP Clean Rebuild rồi chạy lại.",
                    "WP Clean Rebuild",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = true,
                WindowStyle = ProcessWindowStyle.Normal
            };

            Process.Start(startInfo);
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "Không thể khởi động WP Clean Rebuild.\n\n" + ex.Message,
                "WP Clean Rebuild",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }
}
