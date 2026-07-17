> **Resuming work?** Read `RESUME.md` for current status, decisions, open items,
> and the next task. This file holds the durable standards/architecture.

KiCad PRISM is a web viewer for KiCad projects. This repository contains the source code of the project

## Output outcome

The main goal is to achieve feature parity with the KiCad eeschema when it comes to passively analyzing the schematics,

1. The web viewer supports the hierarchical structure of the schematics. We start at the top level schematic and can double-left-click to enter this specific instance of the schematic. The subsheets are correctly resolved as in: one subsheet can have multiple instantiations and the designators/references are updated accordingly. If we are in a subsheet we can go up the hierarchy with a button in the UI. There is a back button in the UI so we can backtrack. There is a side panel where the hierarchy is resolved and displayed. `spec/UI_features_missing.jpg` contains a screenshot of the official KiCad eeschema window where the missing elements are highlighted with red boxes and red text.


2. KiCAD Prism is missing project upload via the web client. Projects can only be cloned from github.

## Resources

For 1. we refer to the official KiCad mirror located in `spec/kicad-source-mirror`. The repository contains so many files that one needs to be strategic what to analyize. Only consult relevant files and do not analyitze the full repo. Confirm with me if the scope needs to be extended. Most information for resolution of the hierarchy should be in this file: `spec/kicad-source-mirror/eeschema/schematic.cpp`.

## Testing
Testing functionality is done by the user and not by Claude. After every iteration the main test should be that the that containers starts and all the processes (backend, frontend) are alive and respond.