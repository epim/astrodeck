// next/hubs/rig/profiles - the PROFILES sheet's body, rebuilt (wave R7,
// T-R7-17).
//
// The mount file `hubs/rig/sheets/profiles.tsx` imports from here and nowhere
// else, so `components/settings/ProfileList.tsx` is no longer reachable from
// that sheet. One next-side importer of the legacy module remains and is a
// named follow-up, not this task's file: `hubs/rig/devices/rigConnect.ts:23`
// still pulls `waitForProfileActive` from it and should be switched to
// `./profileActive` (identical behaviour, pinned by this area's drift test).

export { ProfilesEditor } from "./ProfilesEditor";
export { ProfileCard } from "./ProfileCard";

// Re-exported rather than imported from the legacy panel (wave plan section
// 2.1's finding, ruling 6): pulling a helper out of a presentation module drags
// that module's whole render tree into the lazily split next bundle.
export { waitForProfileActive } from "./profileActive";

export {
  EMPTY_TITLE, LOAD_FAILED, PROFILES_CAP, errText, overridesTail, profileSubline,
} from "./profilesModel";
