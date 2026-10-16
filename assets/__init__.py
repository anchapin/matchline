"""Runtime data shipped with the wheel (#1070, #1071).

The license ledger gates detector artifacts (#751) and the gbXML/detector-output
schemas validate exports and loads. They used to live at the repo root, which
only worked for editable installs; a wheel install had none of them. Everything
under this package ships as package-data and is read via the package directory.
"""
