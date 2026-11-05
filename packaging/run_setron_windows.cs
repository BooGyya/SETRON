using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class SetronLauncher
{
    [STAThread]
    private static int Main()
    {
        string folder = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "dist", "SETRON");
        string executable = Path.Combine(folder, "SETRON.exe");
        if (!File.Exists(executable))
        {
            MessageBox.Show(
                "dist\\SETRON\\SETRON.exe를 찾을 수 없습니다. Windows 패키지를 빌드해 주세요.",
                "SETRON", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        try
        {
            var startup = new ProcessStartInfo(executable, GetArguments())
            {
                WorkingDirectory = folder,
                UseShellExecute = false,
            };
            using (Process process = Process.Start(startup))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Win32Exception error)
        {
            MessageBox.Show(error.Message, "SETRON 실행 실패", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    private static string GetArguments()
    {
        // 실행 파일 경로만 떼고, 사용자 인수의 원래 따옴표와 공백을 유지한다.
        string commandLine = Environment.CommandLine;
        int separator = commandLine[0] == '"'
            ? commandLine.IndexOf('"', 1) + 1
            : commandLine.IndexOfAny(new[] { ' ', '\t' });
        return separator > 0 ? commandLine.Substring(separator).TrimStart() : string.Empty;
    }
}
