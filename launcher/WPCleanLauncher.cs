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
            string launcher = Path.Combine(root, "GIAODIEN.bat");

            if (!File.Exists(launcher))
            {
                MessageBox.Show(
                    "Khong tim thay GIAODIEN.bat canh WP-Clean-Rebuild.exe.\n\nHay dat file EXE trong thu muc goc cua WP Clean Rebuild roi chay lai.",
                    "WP Clean Rebuild",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = "/d /c \"\"" + launcher + "\"\"",
                WorkingDirectory = root,
                UseShellExecute = true,
                WindowStyle = ProcessWindowStyle.Normal
            };

            Process.Start(startInfo);
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "Khong the khoi dong WP Clean Rebuild.\n\n" + ex.Message,
                "WP Clean Rebuild",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }
}
