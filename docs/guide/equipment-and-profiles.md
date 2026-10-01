<a id="equipment--profiles"></a>

# Equipment and profiles

These procedures use the classic interface at `#/classic`. The alternative interface has its own [Rig and settings layout](next-ui.md). Connecting equipment and managing profiles require admin access (`config.backend`) and a direct connection to the controller, not the relay.

<a id="step-1--declare-your-drivers"></a>

## Step 1: Declare your drivers

1. Open **Settings**, then **Connect**, then **Backend Drivers**.
2. In **Add driver**, choose **Type**, enter **Host** and **Port**, and optionally enter **Label (optional)**. Choose the backend you actually run: **NINA**, **Alpaca server**, or **PHD2**. Press **Add**.
3. For native hardware attached to this computer, press **Scan for USB/serial hardware**. Review the results and add only the devices you intend to use.
4. Use **Probe** to refresh a configured backend's offers. Discovery is evidence that a device was found, not a successful observing test.

NINA's Advanced API normally uses port 1888; PHD2 normally uses 4400. Use the port your backend actually listens on. Alpaca discovery can find an advertised server without assuming a fixed port.

<a id="step-2--assign-devices-to-roles"></a>

## Step 2: Assign devices to roles

1. Open **Equipment**. For each role, select the intended enabled, reachable driver and, where offered, its device.
2. Check the camera, mount, focuser, filter wheel and guide-camera assignments individually. A guider connection and a guide-camera device are different roles.
3. Read any missing or unreachable-device message before connecting.

<a id="step-3--connect"></a>

## Step 3: Connect

1. In **Rig Actions**, press the **Connect Rig** button, which also displays the number of assigned roles.
2. Read the confirmation before accepting a connection that can operate real motion devices.
3. Check **Link Status** and each device's error message. Partial connection is not a working full rig.

On a fresh disconnected setup, **Simulator** assigns simulator devices. It is a demonstration choice, not a reconnect command for a real rig. **Disconnect** drops the rig and may abort an active sequence; read its confirmation first.

<a id="rotator"></a>

<a id="task-providers-autofocus-polar-align-plate-solve"></a>

## Task providers

Device drivers and task engines are separate. The **Tasks** panel lets you select **Autofocus**, **Polar align**, and **Plate solve** providers. Read the resolved provider and its reason instead of assuming that an installed camera driver supplies every task.

Native guiding and native autofocus require `astrodeck_native`. Published releases do not include it yet (#630). PHD2 can provide guiding; NINA can supply the task providers its backend advertises. ASTAP is a separate executable and star-database installation for standalone solving.

<a id="the-imaging-train-focal-length-and-the-scopes-name"></a>

## The imaging train

1. Open **Settings**, **Connect**, **Imaging train**.
2. Enter **Focal length (mm)** for the complete optical train, including its reducer or extender, and **Telescope name** for the optical tube.
3. Leave **Take sensor details from the camera** enabled when the driver reports correct values; otherwise enter the sensor details deliberately.
4. Set **Guide scope focal length (mm)** for a separate guide train when you use one.

These values feed scale and framing calculations. Check them before treating a failed solve as a device failure.

<a id="related"></a>

<a id="routes"></a>

## Profiles

1. With the intended devices connected, open **Settings**, **Profiles**.
2. Under **Save Current Rig**, enter **Profile name**, then press **Save Rig**.
3. Review the saved profile and press **Activate** when you want it to connect those devices. Read the confirmation if another rig or operation is active.
4. Use **Reconnect** on the active profile to bring it back after a connection failure.

Activating a profile also selects it for automatic connection at server startup. **Update** overwrites the profile from the connected rig; **Delete** removes the saved profile. Export a copy before replacing a configuration you may need again.

## Native hardware and backend limits

| Path | Implemented behavior and evidence limit |
|---|---|
| ZWO AM5 family | USB serial mount driver; recorded native movement/tracking validation names the AM5N |
| ZWO ASI cameras | ASICamera2 enumeration and capture; native cooler and anti-dew control are absent |
| Player One cameras | SDK camera path includes cooling and dew controls where advertised; recorded checks name the Poseidon-M Pro |
| ZWO EAF / CAA | Native accessory drivers exist; recorded checks and model coverage differ |
| Wanderer Snowflake | Native serial filter-wheel driver |
| ASCOM devices | Use an Alpaca backend, or the Windows COM-host route where configured |

See the [hardware page](https://epim.github.io/astrodeck/hardware.html) for the recorded checks. Supported means an implemented path; it does not certify every model, feature or full unattended night. No hardware was exercised to write this guide.

For ASIAIR owners, Atlas planning and weather are usable alongside the box. The optional `asiair` extra adds an experimental libasi backend. It has not been validated on real hardware; filter-wheel and rotator control are not established. Do not replace a working ASIAIR session on the assumption of feature parity.
