# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Voltcraft Live contributors
#
# This file is part of Voltcraft Live.
# Voltcraft Live is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Voltcraft Live is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Voltcraft Live. If not, see <https://www.gnu.org/licenses/>.

import importlib.util
import json
from pathlib import Path
import sys
spec=importlib.util.spec_from_file_location("voltcraft_live",Path(__file__).resolve().parent.parent/"voltcraft_live.py")
v=importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
if sys.argv[1]=="html":
    print(v.HTML)
else:
    frame=v.demo_frame(0)
    # A generated one-channel frame exercises the inactive-channel controls.
    frame=frame[:46]+b"1000"+frame[50:99]+frame[99:4099]
    print(json.dumps({"sequence":1,"identity":"Synthetic test instrument", "mode":"legacy",
                     "paused":False,"error":"","remote_available":True,"age":0,"fps":2.8,
                     "frame":v.view_frame(frame)}))
