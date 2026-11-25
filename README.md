<img src=".github/icon.png" alt="" width="96" align="right">

# aaspire-tools

The tools of [AASPIRE](https://github.com/AAU-AASPIRE/aaspire): the IEC 61499 library a module is built
from, and the Python packages that generate its program, describe it in an AAS, plan new skills for it,
reconfigure it online and run its skills. They are used together, so they live together: one install,
one test suite, one version.

| | Package | Pillar | What it does |
| --- | --- | --- | --- |
| <img src=".github/icons/modlib.png" alt="" width="40"> | `ModLib/` | Reconfiguration | The 4diac project of the module-independent IEC 61499 types (package `modlib`): the module's PackML state manager and occupation, the skill core and run, flow blocks, parameters, IO routing, adapters. `stdtypes/` holds the standard types every generated project declares |
| <img src=".github/icons/modgen.png" alt="" width="40"> | `modgen` | Reconfiguration | Generates a module's 4diac project (equipment IO, skill primitives, module level skills, system) from its spec |
| <img src=".github/icons/modreg.png" alt="" width="40"> | `modreg` | Integration | Builds, checks, derives and publishes the AASs of modules, products and processes; the registration and skill service that matches required against offered capabilities |
| <img src=".github/icons/modplan.png" alt="" width="40"> | `modplan` | Planning | Plans new skills for a module from its Capability Description: HTN planning with goal regression, parallel steps and a checker; PDDL and HDDL exported |
| <img src=".github/icons/modsync.png" alt="" width="40"> | `modsync` | Reconfiguration | Keeps a running IEC 61499 program in step with its AAS: reads it back over the management protocol, verifies, changes it online and records what was built |
| <img src=".github/icons/modlink.png" alt="" width="40"> | `modlink` | Integration | Runs a module's skills over its OPC UA server, as an orchestrator does, with the interface read from the module's AAS |
| <img src=".github/icons/modpy.png" alt="" width="40"> | `modpy` | Execution | Runs a module in Python instead of IEC 61499, with the same OPC UA interface and state machines, for devices FORTE does not reach (the KUKA robots) |

## Install and test

The tools expect the other repositories beside this one (`AAU-AASPIRE/`, cloned by
[aaspire's workspace script](https://github.com/AAU-AASPIRE/aaspire#working-on-all-of-them)): the
vocabulary in `ontologies` (or `$AASPIRE_ONTOLOGIES`), the line's module specs in `aseptic-line/cell`
(or `./cell`, or `$MODGEN_CELL`), FORTE in `forte-runtime`.

```powershell
python -m pip install -e ".[test]"       # editable: modgen reads ModLib from this folder
python -m pytest                         # modlink's tests also want the HMI (iec61499-opcua-hmi) beside this repository
```

## Use

Run from a line's repository (the module specs in `cell/modules`, e.g. the aseptic line):

```powershell
python -m modgen                                                        # every cell/modules/*.yaml -> cell/control/<project>
python -m modreg profile filling --target pi                            # the profiles of a module and its components -> profiles/
python -m modreg build profiles/FillingModuleAAS.json                   # the AAS a profile describes -> aas/
python -m modreg check profiles/FillingModuleAAS.json --ontology ../ontologies/ARSO
python -m modreg serve --ontology ../ontologies/ARSO --basyx http://localhost:8081 --discover   # the registration and skill service, :8090
python -m modplan plan aas/ --capability Filling --value FillVolume=2.0 --name PlannedFilling --write
python -m modplan export aas/ --capability Filling --value FillVolume=2.0 --as hddl --out planning/   # for ENHSP, Aries ([planners])
python -m modsync pull --host 192.168.0.203                             # which module is this, and how does it drift from its spec?
python -m modsync reconfigure http://localhost:8081 --shell FillingModuleAAS --dry-run   # bring the module to its AAS
python -m modpy serve cell/modules/python/loading.yaml cell/modules/python/unloading.yaml --port 4840
```

```python
from modlink import Module                # occupy, set parameters, start, follow the states, release
```

The generated projects are built into a FORTE by [forte-runtime](https://github.com/AAU-AASPIRE/forte-runtime);
modsync talks to FORTE with [iec61499-mgmt-py](https://github.com/AAU-AASPIRE/iec61499-mgmt-py); the
AASs are built with the lab's model ([aas-model](https://github.com/AAU-AASPIRE/aas-model)) and checked
against ARSO ([ontologies](https://github.com/AAU-AASPIRE/ontologies)); the agents
([AAS-MAS](https://github.com/AAU-AASPIRE/AAS-MAS)) run their steps with modlink.

Until 25 November 2026 the packages were repositories of their own (modreg, modsync, modplan, modlink,
module-py, iec61499-skill-lib); their histories are this one's.

## Licence

MIT.
