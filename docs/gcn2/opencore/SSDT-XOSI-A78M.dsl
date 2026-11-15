/*
 * SSDT-XOSI for the MSI A78M-E35 V2 (MS-7721, AMI BIOS V6.5) Kaveri test desktop.
 *
 * The DSDT's OSFL() sets OSVR from _OSI("Windows xxxx") checks. macOS only answers _OSI("Darwin"), so OSVR stays 3,
 * and HPET._STA then reports the HPET disabled (it needs OSVR >= 0x0C, "Windows 2006") and clears its enable bits
 * (HPAD &= ~0x1F), as on the D509BA and the Kabini HPs. The only other OSVR >= 0x0C check is ECIR._STA, the Super I/O's
 * consumer IR receiver.
 *
 * Together with the OpenCore ACPI patch "_OSI" -> "XOSI" (DSDT only), the firmware's OS checks see Windows 10 under
 * macOS (OSVR = 0x10, "Windows 2013") and every other OS the real _OSI. Unlike SSDT-XOSI.dsl (D509BA), no XHCF: this
 * board has no XHCD / D3cold power gating, and its XHC0/XHC1 nodes have no _PS0/_PS3.
 * Compile: iasl SSDT-XOSI-A78M.dsl
 */
DefinitionBlock ("", "SSDT", 2, "CRRED", "XOSI", 0x00002000)
{
    Method (XOSI, 1, NotSerialized)
    {
        // Windows _OSI strings, oldest to newest (Microsoft "How to Identify the Windows Version in ACPI by Using _OSI").
        Local0 = Package ()
            {
                "Windows 2001",
                "Windows 2001.1",
                "Windows 2001 SP1",
                "Windows 2001 SP2",
                "Windows 2001 SP3",
                "Windows 2006",
                "Windows 2006 SP1",
                "Windows 2006 SP2",
                "Windows 2009",
                "Windows 2012",
                "Windows 2013",
                "Windows 2015"
            }
        If (_OSI ("Darwin"))
        {
            Return ((Ones != Match (Local0, MEQ, Arg0, MTR, Zero, Zero)))
        }
        Else
        {
            Return (_OSI (Arg0))
        }
    }
}
