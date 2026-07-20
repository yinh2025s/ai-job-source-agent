function parseDataAttributeToIntArray(value) {
  return value.split("|").map(Number);
}

const listing = document.querySelector(".job-listing");
const hideFacets = listing.getAttribute("data-hide-facets") === "True";
const useWorkDay = listing.getAttribute("data-use-workday");
const inventoryRequest = {
  Locations: parseDataAttributeToIntArray(
    listing.getAttribute("data-locations")
  ),
  Categories: parseDataAttributeToIntArray(
    listing.getAttribute("data-categories")
  ),
  HideFacets: hideFacets,
  UseWorkDay: useWorkDay
};

fetch('/api/jobs/JobListing', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
  body: JSON.stringify(inventoryRequest)
}).then((response) => response.json()).then((payload) => {
  render(payload.Jobs, payload.Categories, payload.Locations, payload.TotalJobCount);
});

function renderJob(job) {
  return job.JobTitle + job.Reqnumber;
}
