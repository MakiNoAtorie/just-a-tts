"""Writes the Quick Actions into ~/Library/Services. Rerun after editing; then run pbs -update."""
import os, plistlib, uuid
U = "http://127.0.0.1:8765"
# send PATH BODY: posts to LocalTTS, starting it quietly first if it is not running.
SEND = f"""send() {{
  printf %s "$2" | curl -sf -o /dev/null --data-binary @- "{U}$1" && return
  open -ga LocalTTS --args --background
  for i in $(seq 40); do
    sleep 0.5
    printf %s "$2" | curl -sf -o /dev/null --data-binary @- "{U}$1" && return
  done
}}
"""
# Tries the rendered HTML first (works behind a login, needs "Allow JavaScript from Apple Events"
# in the browser's Developer menu), then falls back to having LocalTTS fetch the URL.
PAGE = SEND + """app=$(lsappinfo info -only name "$(lsappinfo front)" | cut -d'"' -f4)
js='document.documentElement.outerHTML'
case "$app" in
  Safari*)
    html=$(osascript -e "tell application \\"$app\\" to do JavaScript \\"$js\\" in front document" 2>/dev/null)
    url=$(osascript -e "tell application \\"$app\\" to get URL of front document") ;;
  *)
    html=$(osascript -e "tell application \\"$app\\" to execute active tab of front window javascript \\"$js\\"" 2>/dev/null)
    url=$(osascript -e "tell application \\"$app\\" to get URL of active tab of front window") ;;
esac
if [ ${#html} -gt 200 ]; then send /html "$html"
elif [ -n "$url" ]; then send /page "$url"
fi"""
SERVICES = [
    ("Read Aloud", True, SEND + 'send /speak "$(cat)"'),
    ("Read Page Aloud", False, PAGE),
    ("Pause or Resume Reading", False, f"curl -s -o /dev/null -X POST {U}/pause"),
    ("Stop Reading", False, f"curl -s -o /dev/null -X POST {U}/stop"),
]
for name, text_in, cmd in SERVICES:
    d = os.path.expanduser(f"~/Library/Services/{name}.workflow/Contents")
    os.makedirs(d, exist_ok=True)
    svc = {"NSMenuItem": {"default": name}, "NSMessage": "runWorkflowAsService"}
    if text_in:
        svc["NSSendTypes"] = ["public.utf8-plain-text"]
    plistlib.dump({"NSServices": [svc]}, open(f"{d}/Info.plist", "wb"))
    kind = "com.apple.Automator.text" if text_in else "com.apple.Automator.nothing"
    s = {"Container": "List", "Types": ["com.apple.cocoa.string"]}
    action = {
        "AMAccepts": {**s, "Optional": True}, "AMProvides": s, "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {k: {} for k in ("COMMAND_STRING", "CheckedForUserDefaultShell", "inputMethod", "shell", "source")},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {"COMMAND_STRING": cmd, "CheckedForUserDefaultShell": True, "inputMethod": 0, "shell": "/bin/zsh", "source": ""},
        "BundleIdentifier": "com.apple.RunShellScript", "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False, "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"], "Class Name": "RunShellScriptAction",
        "InputUUID": str(uuid.uuid4()).upper(), "OutputUUID": str(uuid.uuid4()).upper(), "UUID": str(uuid.uuid4()).upper(),
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"], "UnlocalizedApplications": ["Automator"],
        "arguments": {}, "isViewVisible": 1, "location": "309.000000:253.000000",
        "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib",
    }
    plistlib.dump({
        "AMApplicationBuild": "523", "AMApplicationVersion": "2.10", "AMDocumentVersion": "2",
        "actions": [{"action": action, "isViewVisible": 1}], "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {}, "applicationPaths": [],
            "inputTypeIdentifier": kind, "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 11, "processesInput": False,
            "serviceInputTypeIdentifier": kind, "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False, "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": False, "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }, open(f"{d}/document.wflow", "wb"))
    print("wrote", d)
