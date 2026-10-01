<a id="site--locations"></a>

<a id="use-my-location-browser-geolocation"></a>

<a id="values-computed-from-your-position"></a>

# Site and locations

Set the telescope's location before using altitude, visibility, twilight or weather calculations. The default site is not your observing location. Admin access is required to edit site and optics settings.

<a id="a-note-on-the-longitude-sign"></a>

## Set a site in the alternative interface

1. Open `#/settings/general`, then **SITES**.
2. Press **+ NEW SITE HERE** and enter **Name**, **Latitude**, **Longitude**, and **Elevation (m)**. Use positive coordinate magnitudes with the correct hemisphere selectors.
3. Press **SAVE SITE**. This saves a library entry.
4. Select the radio control beside the saved site to activate it. Check that the active selection moved to the intended site.

Saving or editing a library entry and selecting the active site are separate operations. The phone location shortcut is for a phone beside the telescope; a remote browser's location is not the rig's location.

<a id="privacy-who-can-see-your-coordinates"></a>

<a id="setting-your-site-manually"></a>

## Setting your site manually in classic

1. Open `#/classic/settings`, then **Connect**, **Observing Site**.
2. Enter **Site name**, **Latitude**, **Longitude**, and **Elevation (m)**.
3. Check the hemisphere selectors, then press **Set site**.
4. Confirm that the active-site readout reflects the saved location.

## Filling it in automatically

In classic, **Use my location** fills the form from browser geolocation when available in a secure context, such as HTTPS or localhost. **Use mount GPS** fills it from a mount that reports a valid location. Review either result, then press **Set site**. The shortcuts do not save the form by themselves.

<a id="use-mount-gps"></a>

## Saved locations

1. In classic **Saved locations**, choose a preset and press **Load selected preset**.
2. Review its fields, then press **Set site** to activate it.
3. To keep a new preset, use **Save as location preset…** and name it. Saving that library entry does not replace the active site by itself.

The classic preset form and the alternative **SITES** sheet both use the saved-location library, but their activation controls differ.

## Horizon and privacy

A saved site's horizon can affect target visibility and motion guards. Review the horizon for the observing position; do not copy someone else's values or infer a safe park position from a site preset.

Precise site details are admin-only by default. Operators can see weather and site-derived planning information; viewers do not receive those capabilities. Raw FITS access is separately controlled. Keep private coordinates and labels out of shared screenshots, exported profiles and support material. See [remote access and roles](remote-access-and-roles.md).
