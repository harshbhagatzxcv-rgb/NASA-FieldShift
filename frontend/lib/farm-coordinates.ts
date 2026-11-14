export type FarmCoordinateKind = "latitude" | "longitude";

export function validateCoordinate(value: string | number, kind: FarmCoordinateKind): string | null {
  if (value === "" || value === null || value === undefined) {
    return "Required";
  }

  const raw = typeof value === "string" ? value.trim() : String(value);
  if (raw === "" || raw === "-" || raw === "." || raw === "-." || raw === "+" || raw === "+.") {
    return "Incomplete coordinate";
  }

  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    return "Not a valid number";
  }

  if (kind === "latitude" && (parsed < -90 || parsed > 90)) {
    return "Latitude must be between -90 and 90";
  }

  if (kind === "longitude" && (parsed < -180 || parsed > 180)) {
    return "Longitude must be between -180 and 180";
  }

  return null;
}

export function buildFarmRequestPayload(args: {
  latitude: string | number;
  longitude: string | number;
  currentCropId: string;
  soilTexture?: string;
  isIrrigated?: boolean;
  farmerPriority?: string;
  demoProfileId?: string | null;
  isCustom?: boolean;
}) {
  const latitudeError = validateCoordinate(args.latitude, "latitude");
  const longitudeError = validateCoordinate(args.longitude, "longitude");

  if (latitudeError || longitudeError) {
    throw new Error(latitudeError ?? longitudeError ?? "Invalid coordinates");
  }

  const latitude = Number(args.latitude);
  const longitude = Number(args.longitude);
  const payload: Record<string, unknown> = {
    latitude,
    longitude,
    current_crop_id: args.currentCropId,
    soil_texture: args.soilTexture || undefined,
    is_irrigated: Boolean(args.isIrrigated),
    farmer_priority: args.farmerPriority || "balanced",
  };

  const isCustom = Boolean(args.isCustom) || !args.demoProfileId || args.demoProfileId === "custom";
  if (!isCustom && args.demoProfileId) {
    payload.demo_profile_id = args.demoProfileId;
  }

  return payload;
}
