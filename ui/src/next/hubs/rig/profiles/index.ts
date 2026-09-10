// next/hubs/rig/profiles - the PROFILES sheet's body, rebuilt (wave R7,
// T-R7-17).
//
// The mount file `hubs/rig/sheets/profiles.tsx` imports from here and nowhere
// else, and since T-R7-21a (item 20) so does `hubs/rig/devices/rigConnect.ts`:
// `components/settings/ProfileList.tsx` is not reachable from anywhere under
// `ui/src/next/**` any more. The two copies of `waitForProfileActive` are
// pinned to each other by this area's drift test.

export { ProfilesEditor } from "./ProfilesEditor";
export { ProfileCard } from "./ProfileCard";

// Re-exported rather than imported from the legacy panel (wave plan section
// 2.1's finding, ruling 6): pulling a helper out of a presentation module drags
// that module's whole render tree into the lazily split next bundle.
export { waitForProfileActive } from "./profileActive";

export {
  EMPTY_TITLE, LOAD_FAILED, PROFILES_CAP, errText, overridesTail, profileSubline,
} from "./profilesModel";
