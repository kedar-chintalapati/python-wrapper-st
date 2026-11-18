# Mac build and test

## What this is

This is a test build of the Python package for the LOST star tracker on Mac.
The script builds the package and checks that it works.

## What you need

- A Mac with macOS 13 or newer, about 2 GB of free disk space, and 10 to 20 minutes.
- Xcode command line tools. Install them in Terminal with `xcode-select --install`.
- [Homebrew](https://brew.sh). Follow the setup instructions there.
- Python 3.10 or newer. Check with `python3 --version`. If Python is missing or older, run `brew install python`.

The script installs the Homebrew packages `cairo`, `eigen` and `groff` if they
are missing. All other build and test tools go into the extracted folder.

## Steps

Save the `.tar.gz` file in Downloads, then run these commands in Terminal.
If your file has a different version number, use it in both names.

```bash
cd ~/Downloads
tar xzf lost_python_api-0.3.1.tar.gz
cd lost_python_api-0.3.1
bash packaging/macos-build-and-test.sh
```

## What to send back

Send `mac-test-report.txt` and the `.whl` file from `wheelhouse/`. Both are
inside the extracted folder. If the script fails, send the report and the wheel
if one was created. The report says which step failed.

## Cleaning up

Delete the extracted folder. If you no longer need the Homebrew packages,
you can run `brew uninstall cairo eigen groff`.
