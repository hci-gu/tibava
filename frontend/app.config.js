const browserLocation =
  typeof window !== "undefined" && window.location ? window.location : null;

const frontendBaseUrl = browserLocation
  ? `${browserLocation.protocol}//${browserLocation.hostname}`
  : "http://localhost";

module.exports = {
  API_LOCATION: `${frontendBaseUrl}:8000`,
  THUMBNAIL_LOCATION: `${frontendBaseUrl}/thumbnails`,
};
