using System.Diagnostics;

#if FAULTS
using System.Collections.Concurrent;
using System.Globalization;
using System.Runtime.InteropServices;
#endif

namespace Platform.Core;

/// <summary>
/// Places where a test can make this process die or stop. Only a test build has them: without FAULTS the compiler leaves
/// every call out, and the checks look for what would be left of them.
/// </summary>
public static partial class Fault
{
    [Conditional("FAULTS")]
    public static void At(string point)
    {
#if FAULTS
        Plan.Reach(point);
#endif
    }

#if FAULTS
    /// <summary>
    /// PLATFORM_FAULT is for example "worker.after_claim:kill:3": the third time that point is reached, the process kills
    /// itself. Several can stand there with commas between them.
    /// </summary>
    static partial class Plan
    {
        static readonly Dictionary<string, (string Action, int Time)> Wanted = Read();
        static readonly ConcurrentDictionary<string, int> Reached = new();

        public static void Reach(string point)
        {
            if (!Wanted.TryGetValue(point, out var fault) || Reached.AddOrUpdate(point, 1, (_, times) => times + 1) != fault.Time)
                return;
            // Written before the signal, so that a test can tell a place that was reached from one that never was. Each
            // process has a file of its own, because two that write into the same one get in each other's way.
            var folder = Environment.GetEnvironmentVariable("PLATFORM_FAULTS") ?? throw new InvalidOperationException("PLATFORM_FAULTS is not set.");
            lock (Wanted)
                File.AppendAllText(Path.Combine(folder, $"{Environment.ProcessId}"), $"{point} {fault.Action}\n");
            var signal = fault.Action switch
            {
                "kill" => 9,
                "stop" => OperatingSystem.IsMacOS() ? 17 : 19,
                _ => throw new InvalidOperationException("There is no such fault."),
            };
            // Not kill(): that stops the process a moment later, and by then this thread may have done the next step.
            if (Raise(signal) != 0)
                throw new InvalidOperationException("The signal was not sent.");
        }

        static Dictionary<string, (string Action, int Time)> Read() =>
            (Environment.GetEnvironmentVariable("PLATFORM_FAULT") ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries)
                .Select(fault => fault.Split(':'))
                .ToDictionary(part => part[0], part => (part[1], int.Parse(part[2], CultureInfo.InvariantCulture)));

        [LibraryImport("libc", EntryPoint = "raise")]
        private static partial int Raise(int signal);
    }
#endif
}
