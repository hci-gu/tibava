const browserLocation =
  typeof window !== "undefined" && window.location ? window.location : null;

const frontendBaseUrl = browserLocation
  ? `${browserLocation.protocol}//${browserLocation.hostname}`
  : "http://localhost";

module.exports = {
  API_LOCATION: `${frontendBaseUrl}/api`,
  THUMBNAIL_LOCATION: `${frontendBaseUrl}/thumbnails`,
};
