# DUDE Auto-Start Setup

## Created: 2026-08-28

### Files Created:

1. **E:\Dude\C-dude-archive\startup\dude.bat**
   - Batch script to launch DUDE
   - Starts from the correct directory

2. **E:\Dude\C-dude-archive\config\dude_startup.reg**
   - Registry file for auto-start on Windows login
   - Adds DUDE to HKCU\Run key

### How to Enable Auto-Start:

Double-click `E:\Dude\C-dude-archive\config\dude_startup.reg` to merge it into Windows Registry.
This will make DUDE start automatically when you log in.

Alternatively, manually copy dude.bat to Startup folder:
- Press Win+R, type: shell:startup
- Copy dude.bat there

### Note:
Direct write to Startup folder was blocked by security policy.
The batch file is ready — just need to place it or run the registry file.