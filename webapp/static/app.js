const EMPTY_LINE_ITEMS_MESSAGE = "AI analysis will populate line items here.";
const DEFAULT_PROFILE_ID = "";
const DEFAULT_PRICING_REFERENCE_ID = "";
const CSRF_HEADER_NAME = "X-Swooshz-CSRF";
const LEGACY_QUOTE_PRESETS_STORAGE_KEY = "swooshz_quote_detail_presets_v1";
const LAST_SELECTION_STORAGE_KEY = "swooshz_last_selection_v1";
const QUOTE_SESSION_STORAGE_KEY = "swooshz_quote_session_v1";
const WORKSPACE_VIEW_STORAGE_KEY = "swooshz_workspace_view_v1";
const DASHBOARD_OPERATION_STORAGE_KEY = "swooshz_dashboard_operation_v1";
const DASHBOARD_OPERATION_MAX_AGE_MS = 15 * 60 * 1000;
const QUOTE_SESSION_FILE_DB_NAME = "swooshz_quote_session_files_v1";
const QUOTE_SESSION_FILE_STORE_NAME = "reference_files";
const QUOTE_SESSION_FILE_DB_VERSION = 1;
const QUOTE_SESSION_STATE_VERSION = 5;
const OUTPUT_SORT_MODES = ["pricing_reference", "category", "name", "category_name"];
const ANALYSIS_MODE_STANDARD = "standard";
const ANALYSIS_MODE_HIGH_QUALITY = "high_quality";
const ANALYSIS_CREDIT_COSTS = {
  [ANALYSIS_MODE_STANDARD]: 1,
  [ANALYSIS_MODE_HIGH_QUALITY]: 3,
};
const ANALYSIS_WAIT_ESTIMATE = "This will take about 10 to 15 mins.";
const PRICING_REFERENCE_SETTINGS_MODE_MANAGE = "manage";
const PRICING_REFERENCE_SETTINGS_MODE_IMPORT = "import";
const FINAL_JOB_STATUSES = new Set(["completed", "degraded", "needs_review", "blocked", "failed"]);
const SERVER_JOB_TYPES = new Set(["draft", "basis_chat", "generate", "generate_pdf"]);
const RESTORABLE_OVERLAYS = new Set([
  "pricing_reference_settings",
  "basis_chat",
  "analysis_confirm",
  "excel_ready",
  "pdf_ready",
]);
const PROFILE_PRESET_PREFIX = "profile:";
const COMPANY_PROFILE_PRESET_PREFIX = "company:";
const COMPANY_PROFILE_EXPORT_SCHEMA = "swooshz.quote-company-profile.v1";
const PRICING_REFERENCE_FILE_ACCEPT = ".xlsx,.csv,.md";
const REFERENCE_FILE_ACCEPT = "image/png,image/jpeg,image/webp,application/pdf,.pdf";
const MAX_PRICING_REFERENCE_FILE_BYTES = 10 * 1024 * 1024;
const MAX_REFERENCE_IMAGES = 8;
const DEFAULT_DATE_LABEL = "Date:";
const DEFAULT_TERMS_HEADING = "Terms & Conditions:";
const DEFAULT_NOTES_HEADING = "Note:";
const DEFAULT_ACCEPTANCE_TEXT = "We accept the quotation amount and the terms";
const DEFAULT_PERSON_LABEL = "Person in charge";
const DEFAULT_STAMP_LABEL = "Company name & stamp";
const DEFAULT_TAX_LABEL = "GST";
const DEFAULT_TAX_RATE = 0.09;
const DEFAULT_CURRENCY_LABEL = "SGD";
const DASHBOARD_DEFAULT_PAGE_SIZE = 5;
const DASHBOARD_PAGE_SIZE_OPTIONS = [5, 10, 20, 0];
const MISSING_PRICING_REFERENCES_MESSAGE = "No pricing references found. Please contact an admin or import a pricing reference in Settings before generating a quote.";
const CUSTOM_CURRENCY_VALUE = "__CUSTOM__";
const CURRENCY_OPTIONS = [
  ["SGD", "Singapore Dollar"],
  ["AUD", "Australian Dollar"],
  ["CNY", "Chinese Yuan"],
  ["EUR", "Euro"],
  ["GBP", "Pound Sterling"],
  ["IDR", "Indonesian Rupiah"],
  ["MYR", "Malaysian Ringgit"],
  ["THB", "Thai Baht"],
  ["USD", "US Dollar"],
];
const DEFAULT_QUOTE_COMPANY_RICH_TEXT = {
  termsHeading: "<div>Terms &amp; Conditions:</div>",
  notesHeading: "<div>Note:</div>",
  acceptanceText: "<div>We accept the quotation amount and the terms</div>",
  personLabel: "<div>Person in charge</div>",
  stampLabel: "<div>Company name &amp; stamp</div>",
  companyDateLabel: "<div>Date:</div>",
  dateLabel: "<div>Date:</div>",
};
const DEFAULT_BOOTH_DIMENSIONS = {
  booth_width: "6",
  booth_depth: "6",
  booth_size: "6m x 6m",
  dimension_source: "default",
};

const QUOTE_COPY = {
  label: "Quotation",
  intakeSubtitle: "Drop reference images or PDFs for a real workspace quote.",
  dropTitle: "Drop reference images or PDFs to start",
  dropMeta: `JPG, PNG, WebP, or PDF. Up to ${MAX_REFERENCE_IMAGES} references, 12 MB each; remote AI analysis sends selected refs to the configured provider.`,
  imageNoun: "reference file",
  analyzeLabel: "Start Analysis",
  fallbackAction: "review the reference files",
};

const SIDE_PANEL_SEQUENCE = ["images", "customer", "quote_company", "basis", "output"];
const RICH_TEXT_SOURCE_IDS = [
  "clientName",
  "clientAttention",
  "clientTitle",
  "clientAddress",
  "projectTitle",
  "projectNumber",
  "headerDetails",
  "termsHeading",
  "paymentTerms",
  "notesHeading",
  "standardNotes",
  "quoteCompanyName",
  "acceptanceText",
  "companySignatory",
  "companyTitle",
  "companyDateLabel",
  "personLabel",
  "stampLabel",
  "dateLabel",
];
const QUOTE_COMPANY_RICH_TEXT_IDS = [
  "headerDetails",
  "termsHeading",
  "paymentTerms",
  "notesHeading",
  "standardNotes",
  "quoteCompanyName",
  "acceptanceText",
  "companySignatory",
  "companyTitle",
  "companyDateLabel",
  "personLabel",
  "stampLabel",
  "dateLabel",
];

const EMPTY_BASIS = {
  surfaces: "",
  counters: "",
  platform: "",
  graphics: "",
  furniture: "",
  electrical: "",
};

const BASIS_FIELDS = [
  ["surfaces", "Surfaces / Structures"],
  ["counters", "Cabinets / Counters"],
  ["platform", "Platform / Flooring"],
  ["graphics", "Graphics / Signage"],
  ["furniture", "Furniture / Plants / AV"],
  ["electrical", "Electrical"],
];

const BASIS_TAGS = [
  ["Include", "Include", "Confirmed in the draft"],
  ["Exclude", "Exclude", "Not included unless requested"],
  ["Custom", "AI Proposal", "Not found in pricing reference"],
  ["Confirm", "Confirm", "Needs include, exclude, or revision"],
];

const state = {
  profileId: "",
  pricingReferenceId: "",
  pricingReferenceSource: "",
  selectedPresetValue: "",
  profiles: [],
  companyProfiles: [],
  workspace: null,
  pricingReferences: [],
  defaultProfileId: DEFAULT_PROFILE_ID,
  defaultPricingReferenceId: DEFAULT_PRICING_REFERENCE_ID,
  images: [],
  headerLogo: null,
  workflowStage: "needs_images",
  activeAppView: "dashboard",
  restorableOverlay: "",
  dashboardOperation: null,
  quoteSessionId: "",
  quoteSessions: [],
  quoteSessionLoadError: "",
  quoteSessionDashboardLoadId: 0,
  quoteSessionDashboardBusy: false,
  quoteSessionRestoreError: "",
  quoteSessionRestoreBusy: false,
  quoteSessionDraftSaveStarted: false,
  quoteSessionRestoredSessionId: "",
  quoteSessionRestoredDraftKey: "",
  dashboardStatusFilter: "all",
  dashboardDateFilter: "all",
  dashboardCustomDateStart: "",
  dashboardCustomDateEnd: "",
  dashboardSortMode: "created",
  dashboardSearch: "",
  dashboardPageSize: DASHBOARD_DEFAULT_PAGE_SIZE,
  dashboardPageIndex: 0,
  dashboardSelectionMode: false,
  dashboardSelectedSessionIds: [],
  dashboardActiveSessionId: "",
  quoteSessionDeletePendingIds: [],
  quoteSessionDeleteBulk: false,
  quoteSessionDeleteBusy: false,
  quoteBasis: { ...EMPTY_BASIS },
  quoteBasisSections: [],
  lineItems: [],
  outputRows: [],
  originalOutputRows: [],
  outputErrors: [],
  outputSortMode: "pricing_reference",
  analysisFindings: [],
  blockingClarificationQuestions: [],
  boothDimensions: { ...DEFAULT_BOOTH_DIMENSIONS },
  originalAnalysisSnapshot: null,
  basisConfirmed: false,
  isAnalysisRunning: false,
  isGenerating: false,
  isPreparingOutput: false,
  aiFailed: false,
  draftSource: "",
  lastAnalysisMode: ANALYSIS_MODE_STANDARD,
  pendingAnalysisMode: ANALYSIS_MODE_STANDARD,
  isBooting: true,
  isPageUnloading: false,
  csrfHeaderName: CSRF_HEADER_NAME,
  csrfToken: "",
  authRequired: false,
  authenticated: false,
  authUser: null,
  browserRecoveryScope: "",
  isRecoveryScopeTransitioning: false,
  pendingFeedback: "",
  activeSidePanel: "images",
  downloadFile: null,
  pdfFile: null,
  outputRevision: 0,
  downloadFileRevision: -1,
  pdfFileRevision: -1,
  pricingMatches: [],
  pricingIssues: [],
  activeJob: null,
  quoteCommercialTouched: {
    quoteCurrency: false,
    quoteExchangeRate: false,
    quoteTaxLabel: false,
    quoteTaxRate: false,
  },
  pendingPricingReference: null,
  editingPricingReferenceId: "",
  editingPricingReferenceSource: "",
  pricingReferenceSettingsMode: PRICING_REFERENCE_SETTINGS_MODE_MANAGE,
  pricingReferenceImportFileSelected: false,
  pricingReferenceImportBusy: false,
  pricingReferenceSaveBusy: false,
  pricingReferenceImportToken: "",
  pricingReferenceEditSnapshot: "",
  pricingReferenceEditUndoStack: [],
  pricingReferenceEditPendingUndoSnapshot: "",
  pricingReferenceTableOpenedSnapshot: "",
  pricingReferenceEditNotice: "",
  pricingReferenceAutoLoadToken: "",
  pricingReferenceSavedNotice: "",
  pricingReferenceDeleteConfirmId: "",
  pricingReferenceDeleteConfirmSource: "",
  pricingReferenceDeleteError: "",
  pricingReferenceDeleteBusy: false,
  profileDeleteConfirmId: "",
  profileDeleteReadOnlyName: "",
  profileDeleteError: "",
  profileLoadConfirmValue: "",
  profileOverwriteConfirmLabel: "",
  profileOverwriteConfirmOptions: null,
  profileSaveBusy: false,
  profileDeleteBusy: false,
  pendingProfilePack: null,
  profileNameMode: "",
  profileNamePendingProfile: null,
  profileNameError: "",
  outputDeleteRowIndex: -1,
  permissions: {
    role: "viewer",
    canManageSettings: false,
    canManagePricingReferences: false,
    canManageProfiles: false,
    canImportPricingReferences: false,
  },
  quoteDateFormat: {
    bold: false,
    italic: false,
    underline: false,
  },
  basisChat: {
    scope: "quote",
    field: "",
    sectionId: "",
    lineIndex: -1,
    line: "",
    quantity: "",
    unit: "",
    quantityLabel: "",
    proposal: null,
  },
};

const qs = (selector) => document.querySelector(selector);
let analysisElapsedTimerId = 0;
const elapsedTimerIds = new Map();
let sessionFileDbPromise = null;
let quoteSessionDraftSaveTimer = null;
let quoteSessionInitialSavePromise = null;

const elements = {
  healthText: qs("#healthText"),
  statusDot: qs("#statusDot"),
  topbarStatus: qs("#topbarStatus"),
  imageIntake: qs("#imageIntake"),
  dropTitle: qs("#dropTitle"),
  dropMeta: qs("#dropMeta"),
  dropzone: qs(".dropzone"),
  imageInput: qs("#imageInput"),
  imageUploadStatus: qs("#imageUploadStatus"),
  fileList: qs("#fileList"),
  quoteDashboardPanel: qs("#quoteDashboardPanel"),
  quoteFlowPanel: qs("#panel-analysis"),
  topbarBrandButton: qs("#topbarBrandButton"),
  topbarAuthState: qs("#topbarAuthState"),
  topbarAuthText: qs("#topbarAuthText"),
  topbarLogoutLink: qs("#topbarLogoutLink"),
  dashboardEmptyNewQuoteButton: qs("#dashboardEmptyNewQuoteButton"),
  backToDashboardButton: qs("#backToDashboardButton"),
  dashboardStatusFilter: qs("#dashboardStatusFilter"),
  dashboardDateFilter: qs("#dashboardDateFilter"),
  dashboardCustomDateRange: qs("#dashboardCustomDateRange"),
  dashboardDateFilterSummary: qs("#dashboardDateFilterSummary"),
  dashboardDateStartInput: qs("#dashboardDateStartInput"),
  dashboardDateEndInput: qs("#dashboardDateEndInput"),
  dashboardSortSelect: qs("#dashboardSortSelect"),
  dashboardSearchInput: qs("#dashboardSearchInput"),
  dashboardPageControls: qs("#dashboardPageControls"),
  dashboardPageSizeSelect: qs("#dashboardPageSizeSelect"),
  dashboardRangeSelect: qs("#dashboardRangeSelect"),
  dashboardSessionsList: qs("#dashboardSessionsList"),
  dashboardSessionCount: qs("#dashboardSessionCount"),
  dashboardEmptyState: qs("#dashboardEmptyState"),
  dashboardEmptyEyebrow: qs("#dashboardEmptyEyebrow"),
  dashboardErrorState: qs("#dashboardErrorState"),
  dashboardErrorText: qs("#dashboardErrorText"),
  dashboardTotalSessions: qs("#dashboardTotalSessions"),
  dashboardGeneratedSessions: qs("#dashboardGeneratedSessions"),
  dashboardExportedSessions: qs("#dashboardExportedSessions"),
  dashboardSidePanel: qs("#dashboardSidePanel"),
  dashboardEmptySelectionPanel: qs("#dashboardEmptySelectionPanel"),
  dashboardSelectedSessionPanel: qs("#dashboardSelectedSessionPanel"),
  dashboardLoadingModal: qs("#dashboardLoadingModal"),
  dashboardLoadingTitle: qs("#dashboardLoadingTitle"),
  dashboardLoadingMessage: qs("#dashboardLoadingMessage"),
  dashboardSelectionToolbar: qs("#dashboardSelectionToolbar"),
  dashboardSelectModeButton: qs("#dashboardSelectModeButton"),
  dashboardSelectionHint: qs("#dashboardSelectionHint"),
  newQuoteButton: qs("#newQuoteButton"),
  sideWorkspace: qs("#sideWorkspace"),
  sideDrawerTitle: qs("#sideDrawerTitle"),
  sideDrawerEyebrow: qs("#sideDrawerEyebrow"),
  sideDrawerSubtitle: qs("#sideDrawerSubtitle"),
  clientName: qs("#clientName"),
  clientAttention: qs("#clientAttention"),
  clientTitle: qs("#clientTitle"),
  clientAddress: qs("#clientAddress"),
  projectTitle: qs("#projectTitle"),
  showName: qs("#showName"),
  quoteDate: qs("#quoteDate"),
  quoteDateFormatButtons: Array.from(document.querySelectorAll("[data-date-format-command]")),
  projectNumber: qs("#projectNumber"),
  headerDetails: qs("#headerDetails"),
  headerLogoInput: qs("#headerLogoInput"),
  headerLogoPreview: qs("#headerLogoPreview"),
  termsHeading: qs("#termsHeading"),
  paymentTerms: qs("#paymentTerms"),
  notesHeading: qs("#notesHeading"),
  standardNotes: qs("#standardNotes"),
  quoteCompanyName: qs("#quoteCompanyName"),
  acceptanceText: qs("#acceptanceText"),
  companySignatory: qs("#companySignatory"),
  companyTitle: qs("#companyTitle"),
  companyDateLabel: qs("#companyDateLabel"),
  personLabel: qs("#personLabel"),
  stampLabel: qs("#stampLabel"),
  dateLabel: qs("#dateLabel"),
  taxLabel: qs("#taxLabel"),
  taxRate: qs("#taxRate"),
  quoteCurrency: qs("#quoteCurrency"),
  quoteCurrencyCustom: qs("#quoteCurrencyCustom"),
  quoteTaxLabel: qs("#quoteTaxLabel"),
  quoteTaxRate: qs("#quoteTaxRate"),
  quoteExchangeRate: qs("#quoteExchangeRate"),
  quoteExchangeRateField: qs("#quoteExchangeRateField"),
  aiFailureBanner: qs("#aiFailureBanner"),
  basisReviewSurface: qs("#basisReviewSurface"),
  resultStatus: qs("#resultStatus"),
  outputStatusPill: qs("#outputStatusPill"),
  outputSourceLabel: qs("#outputSourceLabel"),
  outputTotalLines: qs("#outputTotalLines"),
  outputQuoteExchangeRate: qs("#outputQuoteExchangeRate"),
  messageList: qs("#messageList"),
  matchSummary: qs("#matchSummary"),
  pricingMatchesBody: qs("#pricingMatchesBody"),
  pricingTableWrap: qs("#pricingTableWrap"),
  pricingEmptyState: qs("#pricingEmptyState"),
  pricingReviewMessages: qs("#pricingReviewMessages"),
  profileSelect: qs("#profileSelect"),
  presetSelect: qs("#presetSelect"),
  loadPresetButton: qs("#loadPresetButton"),
  savePresetButton: qs("#savePresetButton"),
  deletePresetButton: qs("#deletePresetButton"),
  importPresetButton: qs("#importPresetButton"),
  importPresetFile: qs("#importPresetFile"),
  exportPresetButton: qs("#exportPresetButton"),
  profileActionsMenuButton: qs("#profileActionsMenuButton"),
  presetActionsMenu: qs("#presetActionsMenu"),
  profileActionsShell: qs(".company-preset-more"),
  resetImagesButton: qs("#resetImagesButton"),
  clearCustomerButton: qs("#clearCustomerButton"),
  clearQuoteCompanyButton: qs("#clearQuoteCompanyButton"),
  analyseAgainButton: qs("#analyseAgainButton"),
  resetQuoteBasisButton: qs("#resetQuoteBasisButton"),
  resetOutputButton: qs("#resetOutputButton"),
  presetStatus: qs("#presetStatus"),
  presetSourceBadge: qs(".company-preset-source-badge"),
  profileDeleteModal: qs("#profileDeleteModal"),
  profileDeleteTitle: qs("#profileDeleteTitle"),
  profileDeleteText: qs("#profileDeleteText"),
  profileDeleteError: qs("#profileDeleteError"),
  cancelProfileDeleteButton: qs("#cancelProfileDeleteButton"),
  confirmProfileDeleteButton: qs("#confirmProfileDeleteButton"),
  profileNameModal: qs("#profileNameModal"),
  profileNameEyebrow: qs("#profileNameEyebrow"),
  profileNameTitle: qs("#profileNameTitle"),
  profileNameText: qs("#profileNameText"),
  profileNameInput: qs("#profileNameInput"),
  profileNameError: qs("#profileNameError"),
  cancelProfileNameButton: qs("#cancelProfileNameButton"),
  confirmProfileNameButton: qs("#confirmProfileNameButton"),
  profileLoadModal: qs("#profileLoadModal"),
  profileLoadTitle: qs("#profileLoadTitle"),
  profileLoadText: qs("#profileLoadText"),
  cancelProfileLoadButton: qs("#cancelProfileLoadButton"),
  confirmProfileLoadButton: qs("#confirmProfileLoadButton"),
  profileOverwriteModal: qs("#profileOverwriteModal"),
  profileOverwriteTitle: qs("#profileOverwriteTitle"),
  profileOverwriteText: qs("#profileOverwriteText"),
  cancelProfileOverwriteButton: qs("#cancelProfileOverwriteButton"),
  confirmProfileOverwriteButton: qs("#confirmProfileOverwriteButton"),
  outputDeleteModal: qs("#outputDeleteModal"),
  outputDeleteTitle: qs("#outputDeleteTitle"),
  outputDeleteText: qs("#outputDeleteText"),
  cancelOutputDeleteButton: qs("#cancelOutputDeleteButton"),
  confirmOutputDeleteButton: qs("#confirmOutputDeleteButton"),
  quoteSessionDeleteModal: qs("#quoteSessionDeleteModal"),
  quoteSessionDeleteTitle: qs("#quoteSessionDeleteTitle"),
  quoteSessionDeleteText: qs("#quoteSessionDeleteText"),
  quoteSessionDeleteError: qs("#quoteSessionDeleteError"),
  cancelQuoteSessionDeleteButton: qs("#cancelQuoteSessionDeleteButton"),
  confirmQuoteSessionDeleteButton: qs("#confirmQuoteSessionDeleteButton"),
  workspacePaneFooter: qs(".workspace-pane-footer"),
  sideBackButton: qs("#sideBackButton"),
  sideNextButton: qs("#sideNextButton"),
  sideDownloadButton: qs("#sideDownloadButton"),
  sideViewPdfButton: qs("#sideViewPdfButton"),
  excelGeneratingModal: qs("#excelGeneratingModal"),
  excelGeneratingEyebrow: qs("#excelGeneratingEyebrow"),
  excelGeneratingTitle: qs("#excelGeneratingTitle"),
  excelGeneratingMessage: qs("#excelGeneratingMessage"),
  excelGeneratingSpinner: qs("#excelGeneratingSpinner"),
  excelGeneratingActions: qs("#excelGeneratingActions"),
  excelGeneratingActionButton: qs("#excelGeneratingActionButton"),
  excelGeneratingCloseButton: qs("#excelGeneratingCloseButton"),
  basisChatOverlay: qs("#basisChatOverlay"),
  basisChatTitle: qs("#basisChatTitle"),
  basisChatContext: qs("#basisChatContext"),
  basisChatMessages: qs("#basisChatMessages"),
  basisChatProposal: qs("#basisChatProposal"),
  basisChatProposalActions: qs("#basisChatProposalActions"),
  basisChatForm: qs("#basisChatForm"),
  basisChatPrompt: qs("#basisChatPrompt"),
  basisChatSendButton: qs("#basisChatSendButton"),
  basisChatApplyButton: qs("#basisChatApplyButton"),
  basisChatKeepButton: qs("#basisChatKeepButton"),
  basisChatCloseButton: qs("#basisChatCloseButton"),
  newPricingReferenceButton: qs("#newPricingReferenceButton"),
  deletePricingReferenceButton: qs("#deletePricingReferenceButton"),
  pricingReferenceModal: qs("#pricingReferenceModal"),
  pricingReferenceForm: qs("#pricingReferenceForm"),
  pricingReferenceName: qs("#pricingReferenceName"),
  exportPricingReferenceButton: qs("#exportPricingReferenceButton"),
  pricingReferenceTemplateButton: qs("#pricingReferenceTemplateButton"),
  pricingReferenceFile: qs("#pricingReferenceFile"),
  pricingReferenceFileName: qs("#pricingReferenceFileName"),
  pricingReferenceImportSetup: qs("#pricingReferenceImportSetup"),
  pricingReferenceMetadataSetup: qs("#pricingReferenceMetadataSetup"),
  pricingReferencePreview: qs("#pricingReferencePreview"),
  pricingReferenceManageTab: qs("#pricingReferenceManageTab"),
  pricingReferenceImportTab: qs("#pricingReferenceImportTab"),
  pricingReferenceManagePanel: qs("#pricingReferenceManagePanel"),
  pricingReferenceImportPanel: qs("#pricingReferenceImportPanel"),
  pricingReferenceManageStatus: qs("#pricingReferenceManageStatus"),
  pricingReferenceNoAccess: qs("#pricingReferenceNoAccess"),
  pricingReferenceEditorBody: qs("#pricingReferenceEditorBody"),
  pricingReferenceDeleteSection: qs("#pricingReferenceDeleteSection"),
  deletePricingReferenceSelect: qs("#deletePricingReferenceSelect"),
  pricingReferenceDeleteConfirm: qs("#pricingReferenceDeleteConfirm"),
  pricingReferenceDeleteConfirmTitle: qs("#pricingReferenceDeleteConfirmTitle"),
  pricingReferenceDeleteConfirmText: qs("#pricingReferenceDeleteConfirmText"),
  pricingReferenceDeleteError: qs("#pricingReferenceDeleteError"),
  cancelPricingReferenceDeleteButton: qs("#cancelPricingReferenceDeleteButton"),
  confirmPricingReferenceDeleteButton: qs("#confirmPricingReferenceDeleteButton"),
  pricingReferenceTableOverlay: qs("#pricingReferenceTableOverlay"),
  pricingReferenceTableSummary: qs("#pricingReferenceTableSummary"),
  pricingReferenceTableBody: qs("#pricingReferenceTableBody"),
  pricingReferenceTableCloseButton: qs("#pricingReferenceTableCloseButton"),
  pricingReferenceAddRowButton: qs("#pricingReferenceAddRowButton"),
  pricingReferenceUndoButton: qs("#pricingReferenceUndoButton"),
  settingsButton: qs("#settingsButton"),
  pricingReferenceTaxLabel: qs("#pricingReferenceTaxLabel"),
  pricingReferenceTaxRate: qs("#pricingReferenceTaxRate"),
  pricingReferenceCurrency: qs("#pricingReferenceCurrency"),
  pricingReferenceCurrencyCustom: qs("#pricingReferenceCurrencyCustom"),
  selectedPricingReferenceSummary: qs("#selectedPricingReferenceSummary"),
  selectedPricingReferenceCurrency: qs("#selectedPricingReferenceCurrency"),
  selectedPricingReferenceTax: qs("#selectedPricingReferenceTax"),
  outputSortMode: qs("#outputSortMode"),
  pricingReferenceTemplateFooter: qs("#pricingReferenceTemplateFooter"),
  pricingReferenceSaveButton: qs("#pricingReferenceSaveButton"),
  pricingReferenceCancelButton: qs("#pricingReferenceCancelButton"),
  pricingReferenceCloseButton: qs("#pricingReferenceCloseButton"),
  analysisConfirmModal: qs("#analysisConfirmModal"),
  analysisConfirmCancelButton: qs("#analysisConfirmCancelButton"),
  analysisConfirmHighQualityButton: qs("#analysisConfirmHighQualityButton"),
  analysisConfirmStartButton: qs("#analysisConfirmStartButton"),
  richTextEditors: Array.from(document.querySelectorAll("[data-rich-text-source]")),
  richTextToolbar: Array.from(document.querySelectorAll("[data-rich-command]")),
};

function normalizeAnalysisMode(value) {
  const text = String(value || "").trim().toLowerCase();
  if (["high_quality", "xhigh", "high_accuracy"].includes(text)) return ANALYSIS_MODE_HIGH_QUALITY;
  return ANALYSIS_MODE_STANDARD;
}

function analysisRunningMessage(mode = ANALYSIS_MODE_STANDARD) {
  return ANALYSIS_WAIT_ESTIMATE;
}

function analysisCreditSuffix(mode = ANALYSIS_MODE_STANDARD) {
  const credits = Number(ANALYSIS_CREDIT_COSTS[normalizeAnalysisMode(mode)]);
  if (!Number.isFinite(credits) || credits <= 0) return "";
  return ` (${credits} ${credits === 1 ? "credit" : "credits"})`;
}

function analysisActionLabel(label, mode = ANALYSIS_MODE_STANDARD) {
  return `${label}${analysisCreditSuffix(mode)}`;
}

function syncAnalysisCreditLabels() {
  if (elements.analysisConfirmStartButton) {
    elements.analysisConfirmStartButton.textContent = analysisActionLabel("Run Analysis", ANALYSIS_MODE_STANDARD);
  }
  if (elements.analysisConfirmHighQualityButton) {
    elements.analysisConfirmHighQualityButton.textContent = analysisActionLabel(
      "Run High Quality",
      ANALYSIS_MODE_HIGH_QUALITY
    );
  }
}

function pricingReferenceSelectValue(reference = {}) {
  const referenceId = String(reference.id || "").trim();
  if (!referenceId) return "";
  const source = reference.source === "company"
      ? "company"
      : reference.source === "local" ? "local" : "bundled";
  return `${source}::${referenceId}`;
}

function pricingReferenceSelectionFromValue(value = "") {
  const text = String(value || "").trim();
  if (!text) return { pricingReferenceId: "", source: "" };
  const delimiterIndex = text.indexOf("::");
  if (delimiterIndex < 0) {
    return { pricingReferenceId: text, source: "" };
  }
  return {
    source: text.slice(0, delimiterIndex) || "bundled",
    pricingReferenceId: text.slice(delimiterIndex + 2),
  };
}

function mergePricingReferences(bundled = []) {
  const seen = new Set();
  return [...bundled].filter((reference) => {
    const source = String(reference?.source || "bundled").trim() || "bundled";
    if (!["bundled", "company", "local"].includes(source)) return false;
    const key = pricingReferenceSelectValue(reference);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function sortedPricingReferencesForDisplay(references = []) {
  return [...references].sort((left, right) => {
    const leftLabel = String(left?.label || left?.id || "").trim();
    const rightLabel = String(right?.label || right?.id || "").trim();
    return leftLabel.localeCompare(rightLabel, undefined, { sensitivity: "base" })
      || String(left?.id || "").localeCompare(String(right?.id || ""), undefined, { sensitivity: "base" });
  });
}

function safeId(value = "", fallback = "item") {
  const slug = String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || fallback;
}

function safeQuoteSessionId(value = "") {
  const text = String(value || "").trim();
  return /^quote-[A-Za-z0-9_-]{3,64}$/.test(text) ? text : "";
}

function randomQuoteSessionToken() {
  const bytes = new Uint8Array(12);
  if (window.crypto?.getRandomValues) {
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 14)}`.slice(0, 24);
}

function newClientQuoteSessionId() {
  return safeQuoteSessionId(`quote-${randomQuoteSessionToken()}`);
}

function newClientJobId() {
  return `job-${randomQuoteSessionToken()}`;
}

function newClientOperationId() {
  return `operation-${randomQuoteSessionToken()}`;
}

function normalizeRestorableOverlay(value = "") {
  const overlay = String(value || "").trim();
  return RESTORABLE_OVERLAYS.has(overlay) ? overlay : "";
}

function normalizeActiveJob(job = {}) {
  if (!job || typeof job !== "object") return null;
  const type = String(job.type || "").trim();
  const id = String(job.id || "").trim();
  if (![...SERVER_JOB_TYPES, "confirm_basis"].includes(type) || !id) return null;
  if (SERVER_JOB_TYPES.has(type) && !/^job-[A-Za-z0-9_-]{8,80}$/.test(id)) return null;
  if (type === "confirm_basis" && !/^operation-[A-Za-z0-9_-]{8,80}$/.test(id)) return null;
  return {
    ...job,
    id,
    type,
    phase: job.phase === "running" ? "running" : "starting",
    startedAt: String(job.startedAt || job.created_at || job.createdAt || new Date().toISOString()),
  };
}

function ensureClientQuoteSessionId() {
  const existingSessionId = safeQuoteSessionId(state.quoteSessionId || "");
  if (existingSessionId) return existingSessionId;
  state.quoteSessionId = newClientQuoteSessionId();
  saveSessionState();
  return state.quoteSessionId;
}

function safeProfileId(value = "", fallback = "company-profile") {
  return safeId(value, fallback);
}

function safeProfileLabel(value = "", fallback = "Company Profile") {
  const label = String(value || "").replace(/\s+/g, " ").trim();
  return neutralizeFormulaText(label || fallback);
}

function profilePresetOptionValue(presetId) {
  return `${PROFILE_PRESET_PREFIX}${presetId}`;
}

function companyProfileOptionValue(profileId) {
  return `${COMPANY_PROFILE_PRESET_PREFIX}${profileId}`;
}

function basisDisplayTitle(value = "") {
  return String(value || "")
    .replace(/\u2013|\u2014/g, "-")
    .replace(/\s*-\s*quote\s+basis\s+to\s+confirm\s*$/i, "")
    .trim();
}

function normalizeCategoryTitle(value = "") {
  const text = basisDisplayTitle(value);
  return text || "General";
}

function sectionTitleKey(value = "") {
  const stopWords = new Set(["and", "or", "the", "of", "for", "with", "by", "to", "in", "at", "per"]);
  return basisDisplayTitle(value)
    .toLowerCase()
    .replace(/\bm\s*(?:2|\^2)\b/g, "sqm")
    .replace(/\bsq\.?\s*m\.?\b/g, "sqm")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((token) => {
      if (!token || stopWords.has(token)) return "";
      if (token.length > 4 && token.endsWith("ies")) return `${token.slice(0, -3)}y`;
      if (token.length > 3 && token.endsWith("s")) return token.slice(0, -1);
      return token;
    })
    .filter(Boolean)
    .join(" ");
}

function referenceSectionTitleAliases(value = "") {
  const title = basisDisplayTitle(value);
  if (!title) return [];
  const aliases = [];
  const addAlias = (alias) => {
    const cleaned = basisDisplayTitle(alias);
    if (!cleaned) return;
    const key = sectionTitleKey(cleaned);
    if (!aliases.some((existing) => sectionTitleKey(existing) === key)) aliases.push(cleaned);
  };
  addAlias(title);
  title.split(/\s*(?:\/|&|\band\b)\s*/i).forEach(addAlias);
  const tokens = sectionTitleKey(title).split(/\s+/).filter(Boolean);
  for (let length = 1; length <= Math.min(3, tokens.length); length += 1) {
    addAlias(tokens.slice(0, length).map((token) => token.charAt(0).toUpperCase() + token.slice(1)).join(" "));
  }
  if (tokens.length >= 2) {
    const acronym = tokens.slice(0, Math.min(4, tokens.length)).map((token) => token.charAt(0).toUpperCase()).join("");
    if (acronym.length >= 2) addAlias(acronym);
  }
  return aliases;
}

function exactPricingReferenceSectionTitle(value = "") {
  const text = basisDisplayTitle(value);
  if (!text) return "";
  const appState = typeof state === "object" && state ? state : {};
  const references = Array.isArray(appState.pricingReferences) ? appState.pricingReferences : [];
  const selectedId = String(appState.pricingReferenceId || "").trim();
  const selectedSource = String(appState.pricingReferenceSource || "").trim();
  const reference = references.find((item) => String(item?.id || "") === selectedId && (!selectedSource || String(item?.source || "") === selectedSource))
    || references.find((item) => String(item?.id || "") === selectedId)
    || null;
  const items = Array.isArray(reference?.items) ? reference.items : [];
  const lookup = new Map();
  const sectionEvidence = new Map();
  items.forEach((item) => {
    const title = basisDisplayTitle(item?.reference_section || item?.source_section || item?.section || "");
    if (!title) return;
    referenceSectionTitleAliases(title).forEach((candidate) => {
      const key = sectionTitleKey(candidate);
      if (key && !lookup.has(key)) lookup.set(key, title);
    });
    const evidenceValues = [
      item?.reference_section,
      item?.source_section,
      item?.section,
      item?.description,
      ...(Array.isArray(item?.aliases) ? item.aliases : []),
      ...(Array.isArray(item?.match_terms) ? item.match_terms : []),
      ...(Array.isArray(item?.object_families) ? item.object_families : []),
      ...(Array.isArray(item?.remarks) ? item.remarks : []),
    ];
    const evidenceTokens = sectionEvidence.get(title) || new Set();
    evidenceValues.forEach((evidence) => {
      sectionTitleKey(evidence).split(/\s+/).filter(Boolean).forEach((token) => evidenceTokens.add(token));
    });
    sectionEvidence.set(title, evidenceTokens);
  });
  const inputKeys = referenceSectionTitleAliases(text).map(sectionTitleKey).filter(Boolean);
  const exact = inputKeys.map((key) => lookup.get(key)).find(Boolean);
  if (exact) return exact;
  const inputTokens = new Set(inputKeys.join(" ").split(/\s+/).filter(Boolean));
  if (!inputTokens.size) return "";
  let bestTitle = "";
  let bestScore = 0;
  let tied = false;
  sectionEvidence.forEach((tokens, title) => {
    let overlap = 0;
    inputTokens.forEach((token) => {
      if (tokens.has(token)) overlap += 1;
    });
    const score = inputTokens.size ? overlap / inputTokens.size : 0;
    if (score >= 0.6 && score > bestScore) {
      bestTitle = title;
      bestScore = score;
      tied = false;
    } else if (score >= 0.6 && score === bestScore && title !== bestTitle) {
      tied = true;
    }
  });
  return tied ? "" : bestTitle;
}

function normalizeQuoteBasisTitle(value = "") {
  return exactPricingReferenceSectionTitle(value) || normalizeCategoryTitle(value);
}

function cleanCustomerQuoteLineText(value = "") {
  let text = normalizeUnit(String(value || "").trim().replace(/\s+/g, " "));
  text = text.replace(/(^|[^A-Za-z0-9])m\s*(?:2|\^2)(?=$|[^A-Za-z0-9])/gi, "$1sqm");
  text = text.replace(/(^|[^A-Za-z0-9])sq\.?\s*m\.?(?=$|[^A-Za-z0-9])/gi, "$1sqm");
  const replacements = [
    /\s+taken\s+from\s+quotation\s+title\s*:?\s*/gi,
    /\s+from\s+quotation\s+title\s*:?\s*/gi,
    /\s+visible\s+in\s+image(?:s)?\s*(?:at\s+)?/gi,
    /\s+as\s+seen\s+in\s+render\s*/gi,
    /\s+as\s+per\s+image\s*/gi,
    /\s+based\s+on\s+uploaded\s+reference\s*/gi,
    /\s+ai\s+detected\s*:?\s*/gi,
    /\s+assumed\s+from\s+image\s*/gi,
    /\s+suggested\s+by\s+image\s*/gi,
    /\s+from\s+reference\s+image\s*/gi,
    /\s+appears\s+to\s+be\s+/gi,
    /\s+likely\s+/gi,
  ];
  replacements.forEach((pattern) => { text = text.replace(pattern, " "); });
  return text.replace(/\s+(:|,|\.)/g, "$1").replace(/:\s*([0-9])/g, " $1").replace(/\s{2,}/g, " ").replace(/^[ ;]+|[ ;]+$/g, "");
}

function pricingReferenceLineText(value = "") {
  return String(value || "").trim().replace(/\s+/g, " ");
}

function bracketedCatalogReferenceParts(value = "") {
  const match = cleanCustomerQuoteLineText(value).match(/^\[\s*(.+?)\s*\](?:\s*-\s*(.*))?$/);
  if (!match) return null;
  const reference = cleanCustomerQuoteLineText(match[1] || "");
  const detail = cleanCustomerQuoteLineText(match[2] || "");
  return reference ? { reference, detail } : null;
}

function outputCatalogDescription(value = {}) {
  const reference = pricingReferenceLineText(value.pricing_reference_description || value.catalog_description || "");
  if (reference) {
    const bracketedReference = bracketedCatalogReferenceParts(reference);
    return bracketedReference?.reference || cleanCustomerQuoteLineText(reference);
  }
  if (value.pricing_keyword) {
    const bracketed = bracketedCatalogReferenceParts(value.text || value.description || "");
    if (bracketed?.reference) return bracketed.reference;
  }
  return cleanCustomerQuoteLineText(value.text || value.description || "");
}

function leadingNumber(value = "") {
  const match = String(value ?? "").replaceAll(",", "").trim().match(/^-?\d+(?:\.\d+)?/);
  if (!match) return null;
  const number = Number(match[0]);
  return Number.isFinite(number) ? number : null;
}

function formatQuantityNumber(value) {
  return Number.isInteger(value) ? value : value;
}

function normalizeQuantityPrefixUnit(value = "") {
  const unit = String(value || "").toLowerCase().replace(/\s+/g, " ").replace(/\.+$/g, "").trim();
  if (["sqm", "m2", "m^2", "sq m", "sq.m", "sq.m."].includes(unit)) return "sqm";
  if (["nos", "no", "pc", "pcs", "piece", "pieces", "unit", "units"].includes(unit)) return "nos";
  if (["lot", "lots"].includes(unit)) return "lot";
  if (["set", "sets"].includes(unit)) return "set";
  if (["each", "ea"].includes(unit)) return "each";
  if (["m length", "m run"].includes(unit)) return "m length";
  return normalizeUnit(value);
}

function leadingQuantityPrefix(value = "") {
  const cleaned = cleanCustomerQuoteLineText(value);
  const match = cleaned.match(/^(\d[\d,]*(?:\.\d+)?)\s+(m\s+(?:length|run)|sq\.?\s*m\.?|m\s*(?:2|\^2)|sqm|nos?\.?|no\.?|pcs?\.?|pieces?|units?|lots?|sets?|each|ea)(?=$|[\s:;,\-.])/i);
  if (!match) return null;
  const quantity = leadingNumber(match[1]);
  if (quantity === null || quantity <= 0) return null;
  let text = cleaned.slice(match[0].length).replace(/^[\s:;,\-.]+/, "").replace(/^of\s+/i, "").trim();
  if (!text) return null;
  return {
    text,
    quantity: formatQuantityNumber(quantity),
    unit: normalizeQuantityPrefixUnit(match[2]),
  };
}

function quantityUnitAliases(unit = "") {
  const normalized = normalizeUnit(unit).toLowerCase().replace(/\s+/g, " ").trim();
  const aliases = new Set();
  if (normalized) aliases.add(normalized);
  if (normalized === "sqm") {
    ["sqm", "m2", "m^2", "sq m", "sq.m", "sq.m."].forEach((alias) => aliases.add(alias));
  }
  if (/^nos?\.?$/.test(normalized)) {
    ["nos", "nos.", "no", "no.", "pcs", "pieces", "units"].forEach((alias) => aliases.add(alias));
  }
  if (normalized === "m length") {
    ["m length", "m"].forEach((alias) => aliases.add(alias));
  }
  return Array.from(aliases).filter(Boolean).sort((a, b) => b.length - a.length);
}

function startsWithQuantityUnit(textAfterNumber = "", unit = "") {
  const after = String(textAfterNumber || "").trimStart().toLowerCase();
  if (!after) return true;
  if (!/^[a-z]/.test(after)) return true;
  return quantityUnitAliases(unit).some((alias) => {
    const escaped = alias.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\\ /g, "\\s+");
    return new RegExp(`^${escaped}(?=$|[^a-z0-9])`, "i").test(after);
  });
}

function stripLeadingQuantityCountFromLineText(text = "", quantity = "", unit = "") {
  const cleaned = cleanCustomerQuoteLineText(text);
  const prefix = leadingQuantityPrefix(cleaned);
  if (prefix) {
    const quantityNumber = leadingNumber(quantity);
    if (quantityNumber === null || Math.abs(Number(prefix.quantity) - quantityNumber) <= 0.0001) {
      return prefix.text;
    }
  }
  const quantityNumber = leadingNumber(quantity);
  if (quantityNumber === null || quantityNumber <= 0 || !cleaned) return cleaned;
  const match = cleaned.replaceAll(",", "").match(/^-?\d+(?:\.\d+)?/);
  if (!match) return cleaned;
  const textNumber = Number(match[0]);
  if (!Number.isFinite(textNumber) || Math.abs(textNumber - quantityNumber) > 0.0001) return cleaned;
  const originalMatch = cleaned.match(/^-?\d[\d,]*(?:\.\d+)?/);
  if (!originalMatch) return cleaned;
  const remainder = cleaned.slice(originalMatch[0].length);
  if (!startsWithQuantityUnit(remainder, unit)) return cleaned;
  return remainder.replace(/^[\s:;,\-]+/, "").trim() || cleaned;
}

function normalizedLineTextQuantityParts(text = "", quantity = "", unit = "") {
  const prefix = leadingQuantityPrefix(text);
  if (prefix) return { ...prefix, fromTextPrefix: true };
  return {
    text: stripLeadingQuantityCountFromLineText(text, quantity, unit),
    quantity,
    unit: normalizeUnit(unit),
    fromTextPrefix: false,
  };
}

function neutralizeFormulaText(value = "") {
  const text = String(value || "").trim();
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
}

function normalizeUnit(value = "") {
  const text = String(value || "").trim();
  const lower = text.toLowerCase().replace(/\s+/g, " ").replace(/^[.\s]+|[.\s]+$/g, "");
  if (["m2", "m^2", "sq m", "sq.m", "sq.m.", "square metre", "square meter", "square metres", "square meters"].includes(lower)) {
    return "sqm";
  }
  if (["m run", "m. run"].includes(lower)) return "m run";
  if (["m length", "m. length"].includes(lower)) return "m length";
  if (["nos", "no", "pc", "pcs", "piece", "pieces", "unit", "units"].includes(lower)) return "nos";
  if (["lot", "lots"].includes(lower)) return "lot";
  if (["set", "sets"].includes(lower)) return "sets";
  return text;
}

function currentProfile() {
  const resolvedProfileId = String(state.profileId || state.defaultProfileId || DEFAULT_PROFILE_ID).trim() || DEFAULT_PROFILE_ID;
  return state.profiles.find((profile) => profile.id === resolvedProfileId)
    || state.profiles.find((profile) => profile.id === DEFAULT_PROFILE_ID)
    || state.profiles[0]
    || {
    id: DEFAULT_PROFILE_ID,
    label: "Quotation Profile",
    description: "",
    quote_detail_presets: [],
  };
}

function defaultPricingReference() {
  const profile = currentProfile();
  const profileReferenceId = String(profile?.default_pricing_reference || "").trim();
  const defaultReferenceId = String((state.profileId ? profileReferenceId : state.defaultPricingReferenceId) || profileReferenceId || DEFAULT_PRICING_REFERENCE_ID).trim();
  return state.pricingReferences.find((reference) => reference.id === defaultReferenceId)
    || state.pricingReferences.find((reference) => reference.id === DEFAULT_PRICING_REFERENCE_ID)
    || state.pricingReferences[0]
    || null;
}

function currentPricingReference() {
  const pricingReferenceId = String(state.pricingReferenceId || "").trim();
  const source = String(state.pricingReferenceSource || "").trim();
  if (!pricingReferenceId) return null;
  return state.pricingReferences.find((reference) => reference.id === pricingReferenceId && (!source || pricingReferenceSelectValue(reference).startsWith(`${source}::`)))
    || state.pricingReferences.find((reference) => reference.id === pricingReferenceId && !source)
    || null;
}

function currentBrowserRecoveryScope() {
  return String(state.browserRecoveryScope || "").trim();
}

function safeLastSelectionJson() {
  const currentScope = currentBrowserRecoveryScope();
  if (!currentScope) return {};
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LAST_SELECTION_STORAGE_KEY) || "null");
    if (!parsed || typeof parsed !== "object") return {};
    if (String(parsed.browserRecoveryScope || "") !== currentScope) return {};
    return parsed;
  } catch {
    return {};
  }
}

function clearLastSelectionState() {
  try {
    window.localStorage.removeItem(LAST_SELECTION_STORAGE_KEY);
  } catch {
    // Ignore storage failures; the quote flow does not depend on last-used defaults.
  }
}

function saveLastSelectionPatch(patch = {}) {
  const currentScope = currentBrowserRecoveryScope();
  if (!currentScope) return;
  try {
    window.localStorage.setItem(LAST_SELECTION_STORAGE_KEY, JSON.stringify({
      ...safeLastSelectionJson(),
      ...patch,
      version: 1,
      browserRecoveryScope: currentScope,
      savedAt: new Date().toISOString(),
    }));
  } catch {
    // Last-used defaults are a convenience only; the quote flow still works without storage.
  }
}

function lastSelectedPricingReference() {
  const saved = safeLastSelectionJson();
  const savedValue = String(saved.pricingReferenceValue || "").trim();
  const savedId = String(saved.pricingReferenceId || "").trim();
  const savedSource = String(saved.pricingReferenceSource || "").trim();
  return state.pricingReferences.find((reference) => pricingReferenceSelectValue(reference) === savedValue)
    || state.pricingReferences.find((reference) => (
      String(reference.id || "").trim() === savedId
      && (!savedSource || pricingReferenceSelectValue(reference).startsWith(`${savedSource}::`))
    ))
    || null;
}

function persistLastPricingReferenceSelection(reference = currentPricingReference()) {
  if (!reference) return;
  const referenceValue = pricingReferenceSelectValue(reference);
  const selection = pricingReferenceSelectionFromValue(referenceValue);
  if (!selection.pricingReferenceId) return;
  saveLastSelectionPatch({
    pricingReferenceId: selection.pricingReferenceId,
    pricingReferenceSource: selection.source || "bundled",
    pricingReferenceValue: referenceValue,
  });
}

function resolvedProfileIdForPayload() {
  return String(state.profileId || state.defaultProfileId || DEFAULT_PROFILE_ID).trim() || DEFAULT_PROFILE_ID;
}

function generationProfileIdForPayload() {
  const preset = selectedPreset();
  if (preset?.source === "company") {
    return safeProfileId(preset.id, resolvedProfileIdForPayload());
  }
  const presetProfileId = String(preset?.profile_id || "").trim();
  return presetProfileId || resolvedProfileIdForPayload();
}

function syncSelectedPricingReference() {
  const selectedReference = currentPricingReference();
  if (selectedReference) {
    state.pricingReferenceId = selectedReference.id || "";
    state.pricingReferenceSource = pricingReferenceSelectionFromValue(pricingReferenceSelectValue(selectedReference)).source;
    return;
  }
  const fallbackReference = defaultPricingReference();
  if (fallbackReference) {
    state.pricingReferenceId = fallbackReference.id || "";
    state.pricingReferenceSource = pricingReferenceSelectionFromValue(pricingReferenceSelectValue(fallbackReference)).source;
    return;
  }
  state.pricingReferenceId = "";
  state.pricingReferenceSource = "";
}

function currentGenerator() {
  const pricingReference = currentPricingReference();
  return {
    ...QUOTE_COPY,
    label: pricingReference?.label || QUOTE_COPY.label,
  };
}

function updateGeneratorCopy() {
  const generator = currentGenerator();
  const atImageLimit = state.images.length >= MAX_REFERENCE_IMAGES;
  if (state.activeSidePanel === "images") elements.sideDrawerSubtitle.textContent = generator.intakeSubtitle;
  elements.dropTitle.textContent = atImageLimit
    ? "Maximum reference files added"
    : state.images.length ? "Add more reference files" : generator.dropTitle;
  elements.dropMeta.textContent = atImageLimit
    ? `Remove one reference to add another. Maximum ${MAX_REFERENCE_IMAGES} reference files.`
    : generator.dropMeta;
}

function setWorkflowStage(stage) {
  state.workflowStage = stage;
  document.body.dataset.workflowStage = stage;
}

function parseTimestampMs(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatElapsedDuration(elapsedMs) {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  const minutesTotal = Math.floor(totalSeconds / 60);
  if (minutesTotal < 60) return `${minutesTotal}:${seconds}`;
  const minutes = String(minutesTotal % 60).padStart(2, "0");
  return `${Math.floor(minutesTotal / 60)}:${minutes}:${seconds}`;
}

function elapsedStartedMs(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return parseTimestampMs(value);
}

function updateElapsedTimer(elementId, startedAt) {
  const targets = [];
  const elapsed = document.getElementById(elementId);
  if (elapsed) targets.push(elapsed);
  document.querySelectorAll("[data-elapsed-timer-id]").forEach((candidate) => {
    if (candidate.dataset.elapsedTimerId === elementId && !targets.includes(candidate)) targets.push(candidate);
  });
  if (!targets.length) return;
  const startedMs = elapsedStartedMs(startedAt);
  const elapsedText = `Elapsed ${formatElapsedDuration(startedMs ? Date.now() - startedMs : 0)}`;
  targets.forEach((target) => {
    target.textContent = elapsedText;
  });
}

function stopElapsedTimer(elementId) {
  const timerId = elapsedTimerIds.get(elementId);
  if (timerId) window.clearInterval(timerId);
  elapsedTimerIds.delete(elementId);
}

function startElapsedTimer(elementId, startedAt = Date.now()) {
  stopElapsedTimer(elementId);
  updateElapsedTimer(elementId, startedAt);
  elapsedTimerIds.set(elementId, window.setInterval(() => updateElapsedTimer(elementId, startedAt), 1000));
}

function activeJobStartedAt(job = state.activeJob) {
  return job?.startedAt || job?.created_at || job?.createdAt || "";
}

function stopAnalysisElapsedTimer() {
  stopElapsedTimer("analysisElapsed");
  analysisElapsedTimerId = 0;
}

function updateAnalysisElapsed(startedAt = activeJobStartedAt()) {
  updateElapsedTimer("analysisElapsed", startedAt);
}

function startAnalysisElapsedTimer(startedAt = activeJobStartedAt()) {
  stopAnalysisElapsedTimer();
  startElapsedTimer("analysisElapsed", startedAt);
  analysisElapsedTimerId = elapsedTimerIds.get("analysisElapsed") || 0;
}

const GENERIC_FAILURE_MESSAGE = "Failed. Please try again. Contact support if this keeps happening.";

function errorReferenceFrom(data = {}) {
  if (!data || typeof data !== "object") return "";
  return String(
    data.error_reference
    || data.errorReference
    || data.result?.error_reference
    || data.result?.errorReference
    || ""
  ).trim();
}

function newClientErrorReference() {
  const bytes = new Uint8Array(4);
  if (window.crypto?.getRandomValues) {
    window.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  return `ERR-${Array.from(bytes).map((byte) => byte.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
}

function genericFailureMessage(data = {}) {
  const reference = typeof data === "string" ? "" : errorReferenceFrom(data);
  return reference ? `${GENERIC_FAILURE_MESSAGE} Reference: ${reference}.` : GENERIC_FAILURE_MESSAGE;
}

function genericFailureMessages(data = {}) {
  return [genericFailureMessage(data)];
}

function aiAnalysisInvalidReferenceMessage(data = {}) {
  const sources = [];
  if (typeof data === "string") sources.push(data);
  if (data && typeof data === "object") {
    ["message", "error", "failure_kind"].forEach((key) => sources.push(data[key]));
    ["errors", "provider_errors", "warnings"].forEach((key) => {
      const value = data[key];
      if (Array.isArray(value)) sources.push(...value);
      else sources.push(value);
    });
  }
  const joined = sources.map((value) => String(value || "").toLowerCase()).join(" ");
  if (
    joined.includes("no usable quote basis")
    || joined.includes("model_output_invalid")
    || joined.includes("returned output that could not be used")
  ) {
    return "The uploaded reference is not a valid quote reference for AI analysis. Upload a valid booth render, plan, or scope reference, then run analysis again.";
  }
  return "";
}

function aiAnalysisFailureMessage(data = {}) {
  const invalidReferenceMessage = aiAnalysisInvalidReferenceMessage(data);
  if (invalidReferenceMessage) {
    const reference = typeof data === "string" ? "" : errorReferenceFrom(data);
    return reference ? `${invalidReferenceMessage} Reference: ${reference}.` : invalidReferenceMessage;
  }
  return genericFailureMessage(data);
}

function fetchFailureLogDetails(url, details = {}) {
  return { url, reason: "fetch_failed", ...details };
}

function setAiStatusBanner(tone, title, message, options = {}) {
  if (tone !== "running") stopAnalysisElapsedTimer();
  const elapsedMarkup = options.elapsed
    ? '<span class="ai-elapsed" id="analysisElapsed" aria-live="polite">Elapsed 0:00</span>'
    : "";
  elements.aiFailureBanner.hidden = false;
  elements.aiFailureBanner.classList.toggle("is-running", tone === "running");
  elements.aiFailureBanner.classList.toggle("is-failure", tone !== "running");
  elements.aiFailureBanner.innerHTML = `
    <strong>${escapeHtml(title)}</strong>
    <span class="ai-status-message">${escapeHtml(message)}</span>
    ${elapsedMarkup}
  `;
}

function showAiRunningBanner(message = ANALYSIS_WAIT_ESTIMATE, startedAt = activeJobStartedAt()) {
  setAiStatusBanner("running", "AI analysis running.", message, { elapsed: true });
  startAnalysisElapsedTimer(startedAt);
}

function showAiFailureBanner(message = GENERIC_FAILURE_MESSAGE) {
  const detail = String(message || "").replace(/^AI analysis failed\.?\s*/i, "").trim() || GENERIC_FAILURE_MESSAGE;
  setAiStatusBanner("failure", "AI analysis failed.", detail);
}

function showAiBlockedBanner(message = "Complete the required details, then retry analysis.") {
  const detail = String(message || "").replace(/^AI analysis blocked\.?\s*/i, "").trim() || "Complete the required details, then retry analysis.";
  setAiStatusBanner("failure", "AI analysis blocked.", detail);
}

function showBlockedAction(message = "Complete the required details, then try again.", options = {}) {
  showAiBlockedBanner(message);
  if (options.details !== false && missingDetailFields().length) setDetailsDrawer(true);
}

function clearAiFailureBanner() {
  stopAnalysisElapsedTimer();
  elements.aiFailureBanner.hidden = true;
  elements.aiFailureBanner.classList.remove("is-running", "is-failure");
  elements.aiFailureBanner.innerHTML = `
    <strong>AI analysis failed.</strong>
    <span>${GENERIC_FAILURE_MESSAGE}</span>
  `;
}

function setDetailsDrawer(open) {
  if (open) {
    setSidePanel(firstMissingDetailsPanel(), { force: true });
  }
}

function formatBytes(bytes) {
  if (!bytes) return "0 KB";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function fileToText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function isAcceptedReferenceFile(file = {}) {
  const type = String(file.type || "").toLowerCase();
  const name = String(file.name || "").toLowerCase();
  return type.startsWith("image/") || type === "application/pdf" || name.endsWith(".pdf");
}

function referenceFileType(entry = {}) {
  const match = String(entry.data_url || "").match(/^data:([^;,]+)/i);
  if (match) return match[1].toLowerCase();
  const type = String(entry.type || "").toLowerCase();
  if (type) return type;
  return String(entry.name || "").toLowerCase().endsWith(".pdf") ? "application/pdf" : "image";
}

function isPdfReference(entry = {}) {
  return referenceFileType(entry) === "application/pdf";
}

function referenceFileTypeLabel(entry = {}) {
  return isPdfReference(entry) ? "PDF" : (entry.type || "image");
}

function referenceFileHasPayload(entry = {}) {
  return Boolean(String(entry?.data_url || "").trim());
}

function hasReferenceFilesForNavigation() {
  return state.images.some((image) => (
    referenceFileHasPayload(image)
    || String(image?.session_file_key || image?.name || "").trim()
  ));
}

function hasReferenceFilesForAnalysis() {
  return state.images.some(referenceFileHasPayload);
}

async function filesToImageEntries(files, limit = MAX_REFERENCE_IMAGES) {
  return Promise.all(
    Array.from(files)
      .filter(isAcceptedReferenceFile)
      .slice(0, limit)
      .map(async (file) => {
        const dataUrl = await fileToDataUrl(file);
        return {
          name: file.name,
          type: referenceFileType({ name: file.name, type: file.type, data_url: dataUrl }),
          size: file.size,
          data_url: dataUrl,
        };
      })
  );
}

function imageDuplicateKey(image = {}) {
  const dataUrl = String(image.data_url || "").trim();
  if (dataUrl) return `data:${dataUrl}`;
  const name = String(image.name || "").trim().toLowerCase();
  const type = String(image.type || "").trim().toLowerCase();
  const size = String(image.size || "").trim();
  return name || type || size ? `file:${name}:${type}:${size}` : "";
}

function uniqueImageEntries(nextImages = [], existingImages = state.images) {
  const seen = new Set(existingImages.map(imageDuplicateKey).filter(Boolean));
  const unique = [];
  let duplicateCount = 0;
  nextImages.forEach((image) => {
    const key = imageDuplicateKey(image);
    if (key && seen.has(key)) {
      duplicateCount += 1;
      return;
    }
    if (key) seen.add(key);
    unique.push(image);
  });
  return { unique, duplicateCount };
}

function imageCapacity(existingImages = state.images) {
  return Math.max(0, MAX_REFERENCE_IMAGES - existingImages.length);
}

function setImageUploadStatus(message = "") {
  if (elements.imageUploadStatus) elements.imageUploadStatus.textContent = message;
}

async function addImagesFromFiles(files) {
  const referenceFiles = Array.from(files).filter(isAcceptedReferenceFile);
  if (!referenceFiles.length) {
    if (Array.from(files).length) setImageUploadStatus("Only JPG, PNG, WebP, or PDF files can be added.");
    return;
  }
  const capacity = imageCapacity();
  if (!capacity) {
    setImageUploadStatus(`Maximum ${MAX_REFERENCE_IMAGES} reference files reached. Remove one before adding more.`);
    return;
  }
  const overflowCount = Math.max(0, referenceFiles.length - capacity);
  const images = await filesToImageEntries(referenceFiles, capacity);
  if (!images.length) return;
  const { unique, duplicateCount } = uniqueImageEntries(images);
  if (!unique.length) {
    const duplicateMessage = duplicateCount ? `${duplicateCount} duplicate file${duplicateCount === 1 ? "" : "s"} skipped.` : "";
    const overflowMessage = overflowCount ? `${overflowCount} file${overflowCount === 1 ? "" : "s"} skipped because the maximum is ${MAX_REFERENCE_IMAGES}.` : "";
    setImageUploadStatus([duplicateMessage, overflowMessage].filter(Boolean).join(" "));
    return;
  }
  state.images = [...state.images, ...unique];
  await persistSessionFiles(sessionFileRecordsFromImages(state.images)).catch(() => {});
  renderFiles();
  setWorkflowStage("ready_to_analyze");
  const generator = currentGenerator();
  const duplicateMessage = duplicateCount ? ` ${duplicateCount} duplicate file${duplicateCount === 1 ? "" : "s"} skipped.` : "";
  const overflowMessage = overflowCount ? ` ${overflowCount} file${overflowCount === 1 ? "" : "s"} skipped because the maximum is ${MAX_REFERENCE_IMAGES}.` : "";
  setImageUploadStatus(`${unique.length} new ${generator.imageNoun}${unique.length === 1 ? "" : "s"} added.${duplicateMessage}${overflowMessage}`);
  syncControlStates();
}

function removeImageAt(index) {
  state.images.splice(index, 1);
  renderFiles();
  if (!state.images.length) {
    setWorkflowStage("needs_images");
    setImageUploadStatus("");
  } else {
    setImageUploadStatus(`${state.images.length} reference file${state.images.length === 1 ? "" : "s"} loaded.`);
  }
  syncControlStates();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sentenceLineBreakText(value) {
  return String(value ?? "").trim().replace(/([.!?])\s+(?=\S)/g, "$1\n");
}

function decodeHtmlEntities(value) {
  return String(value ?? "").replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (match, entity) => {
    const normalized = entity.toLowerCase();
    if (normalized === "amp") return "&";
    if (normalized === "lt") return "<";
    if (normalized === "gt") return ">";
    if (normalized === "quot") return '"';
    if (normalized === "apos") return "'";
    if (normalized === "nbsp") return " ";
    if (normalized.startsWith("#x")) {
      const codePoint = Number.parseInt(normalized.slice(2), 16);
      return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
    }
    if (normalized.startsWith("#")) {
      const codePoint = Number.parseInt(normalized.slice(1), 10);
      return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
    }
    return match;
  });
}

function sanitizeRichTextHtml(value = "") {
  const allowedTags = new Set(["div", "p", "br", "strong", "b", "em", "i", "u"]);
  const droppedContentTags = new Set(["script", "style", "iframe", "object", "embed", "svg", "math"]);
  const droppedVoidTags = new Set(["img"]);
  const tokens = String(value || "").match(/<!--[\s\S]*?-->|<![^>]*>|<\/?[A-Za-z][A-Za-z0-9:-]*\b[^>]*>|[^<]+|</g) || [];
  const droppedStack = [];
  let output = "";

  tokens.forEach((token) => {
    const tagMatch = token.match(/^<\/?([A-Za-z][A-Za-z0-9:-]*)\b[^>]*>$/);
    if (!tagMatch) {
      if (!droppedStack.length && !token.startsWith("<!")) {
        output += escapeHtml(decodeHtmlEntities(token));
      }
      return;
    }

    const tag = tagMatch[1].toLowerCase();
    const isClosing = token.startsWith("</");
    const isSelfClosing = /\/\s*>$/.test(token) || tag === "br";

    if (droppedVoidTags.has(tag)) return;
    if (droppedContentTags.has(tag)) {
      if (!isClosing && !isSelfClosing) droppedStack.push(tag);
      if (isClosing) {
        const lastIndex = droppedStack.lastIndexOf(tag);
        if (lastIndex >= 0) droppedStack.splice(lastIndex, 1);
      }
      return;
    }
    if (droppedStack.length) return;
    if (!allowedTags.has(tag)) return;
    if (tag === "br") {
      output += "<br>";
      return;
    }
    output += isClosing ? `</${tag}>` : `<${tag}>`;
  });

  return output;
}

function normalizeTextNewlines(value) {
  return String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/\\n/g, "\n");
}

function renderInlineMarkdown(value) {
  let html = escapeHtml(value);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return html;
}

function isMarkdownTableSeparator(line = "") {
  return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(String(line || "").trim());
}

function splitMarkdownTableRow(line = "") {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(lines) {
  const headers = splitMarkdownTableRow(lines[0]);
  const rows = lines.slice(2).map(splitMarkdownTableRow).filter((row) => row.length);
  if (!headers.length || !rows.length) return `<p>${renderInlineMarkdown(lines[0] || "")}</p>`;
  return `
    <table>
      <thead>
        <tr>${headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr>
      </thead>
      <tbody>
        ${rows.map((row) => `
          <tr>${headers.map((_, index) => `<td>${renderInlineMarkdown(row[index] || "")}</td>`).join("")}</tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderPlainText(value) {
  const lines = normalizeTextNewlines(value)
    .split("\n")
    .map((line) => line.trim());
  const chunks = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line) {
      index += 1;
      continue;
    }

    if (line.includes("|") && isMarkdownTableSeparator(lines[index + 1] || "")) {
      const tableLines = [line, lines[index + 1]];
      index += 2;
      while (index < lines.length && lines[index] && lines[index].includes("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      chunks.push(renderMarkdownTable(tableLines));
      continue;
    }

    if (/^-\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^-\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^-\s+/, ""));
        index += 1;
      }
      chunks.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }

    chunks.push(`<p>${renderInlineMarkdown(line)}</p>`);
    index += 1;
  }
  return chunks.length ? chunks.join("") : "<p></p>";
}

function splitLines(value) {
  return normalizeTextNewlines(value)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function normalizeTaxLabel(value = "") {
  return String(value || "").trim().toUpperCase() === "VAT" ? "VAT" : DEFAULT_TAX_LABEL;
}

function normalizeTaxRate(value, fallback = DEFAULT_TAX_RATE) {
  const number = Number(String(value ?? "").replace("%", "").trim());
  if (!Number.isFinite(number)) return fallback;
  const rate = number > 1 ? number / 100 : number;
  return Math.min(1, Math.max(0, rate));
}

function taxRatePercentText(value = DEFAULT_TAX_RATE) {
  const percent = normalizeTaxRate(value, DEFAULT_TAX_RATE) * 100;
  return Number.isInteger(percent) ? String(percent) : String(Number(percent.toFixed(2)));
}

function taxRateFromPercentInput(value, fallback = DEFAULT_TAX_RATE) {
  const number = Number(String(value ?? "").replace("%", "").trim());
  if (!Number.isFinite(number)) return fallback;
  return Math.min(1, Math.max(0, number / 100));
}

function normalizeCurrencyLabel(value = DEFAULT_CURRENCY_LABEL) {
  const normalized = String(value || "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z]/g, "");
  if (["S", "SG", "SGD", "SINGAPOREDOLLAR", "SINGAPOREDOLLARS"].includes(normalized)) return "SGD";
  if (["US", "USD", "USDOLLAR", "USDOLLARS"].includes(normalized)) return "USD";
  if (["EURO", "EUROS", "EUR"].includes(normalized)) return "EUR";
  if (["GBP", "POUND", "POUNDS", "STERLING"].includes(normalized)) return "GBP";
  if (["MYR", "RM", "MALAYSIARINGGIT", "RINGGIT"].includes(normalized)) return "MYR";
  if (["AUD", "AUSTRALIANDOLLAR", "AUSTRALIANDOLLARS"].includes(normalized)) return "AUD";
  if (["CNY", "RMB", "YUAN", "RENMINBI"].includes(normalized)) return "CNY";
  if (["IDR", "RUPIAH"].includes(normalized)) return "IDR";
  if (["THB", "BAHT"].includes(normalized)) return "THB";
  return /^[A-Z]{3}$/.test(normalized) ? normalized : DEFAULT_CURRENCY_LABEL;
}

function selectedPricingReferenceTax() {
  const reference = currentPricingReference();
  return {
    label: normalizeTaxLabel(reference?.tax?.label),
    rate: normalizeTaxRate(reference?.tax?.rate, DEFAULT_TAX_RATE),
  };
}

function selectedPricingReferenceCurrency() {
  const reference = currentPricingReference();
  return normalizeCurrencyLabel(reference?.currency);
}

function selectedPricingReferenceTaxText() {
  const tax = selectedPricingReferenceTax();
  return `${tax.label} ${taxRatePercentText(tax.rate)}%`;
}

function pricingReferenceContextPillsHtml() {
  return `
    <span class="pricing-reference-context-pills" aria-label="Selected pricing reference currency and tax">
      <span class="pricing-reference-divider" aria-hidden="true"></span>
      <span class="pricing-reference-meta-item">
        <svg class="pricing-reference-meta-icon" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
          <path d="M4 7h15a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a3 3 0 0 1 3-3h11"></path>
          <path d="M16 13h.01"></path>
          <path d="M7 7l9-3"></path>
        </svg>
        <span data-reference-basis-currency>${escapeHtml(selectedPricingReferenceCurrency())}</span>
      </span>
      <span class="pricing-reference-divider" aria-hidden="true"></span>
      <span class="pricing-reference-meta-item">
        <svg class="pricing-reference-meta-icon" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
          <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z"></path>
          <path d="M7.5 7.5h.01"></path>
        </svg>
        <span data-reference-basis-tax>${escapeHtml(selectedPricingReferenceTaxText())}</span>
      </span>
    </span>
  `;
}

function quoteCommercialContextPillsHtml() {
  return `
    <span class="pricing-reference-context-pills" aria-label="Quote currency, tax, and exchange rate">
      <span class="pricing-reference-divider" aria-hidden="true"></span>
      <span class="pricing-reference-meta-item">
        <svg class="pricing-reference-meta-icon" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
          <path d="M4 7h15a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a3 3 0 0 1 3-3h11"></path>
          <path d="M16 13h.01"></path>
          <path d="M7 7l9-3"></path>
        </svg>
        <span data-quote-currency>${escapeHtml(collectQuoteCurrency())}</span>
      </span>
      <span class="pricing-reference-divider" aria-hidden="true"></span>
      <span class="pricing-reference-meta-item">
        <svg class="pricing-reference-meta-icon" viewBox="0 0 24 24" focusable="false" aria-hidden="true">
          <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z"></path>
          <path d="M7.5 7.5h.01"></path>
        </svg>
        <span data-quote-tax>${escapeHtml(quoteCommercialTaxText())}</span>
      </span>
      <span class="pricing-reference-divider" aria-hidden="true"></span>
      <span class="pricing-reference-meta-item">
        <span class="pricing-reference-meta-icon pricing-reference-rate-icon" aria-hidden="true">FX</span>
        <span data-quote-exchange-rate>${escapeHtml(quoteExchangeRateText())}</span>
      </span>
    </span>
  `;
}

function syncPricingReferenceContextPills(currency = selectedPricingReferenceCurrency(), taxText = selectedPricingReferenceTaxText()) {
  if (typeof document === "undefined") return;
  document.querySelectorAll("[data-reference-basis-currency]").forEach((element) => {
    element.textContent = currency;
  });
  document.querySelectorAll("[data-reference-basis-tax]").forEach((element) => {
    element.textContent = taxText;
  });
}

function supportedCurrencyLabel(value = DEFAULT_CURRENCY_LABEL) {
  const normalized = normalizeCurrencyLabel(value);
  return isStandardCurrencyCode(normalized) ? normalized : DEFAULT_CURRENCY_LABEL;
}

function isStandardCurrencyCode(value = "") {
  const normalized = String(value || "").trim().toUpperCase();
  return CURRENCY_OPTIONS.some(([code]) => code === normalized);
}

function isValidCurrencyCode(value = "") {
  return /^[A-Z]{3}$/.test(String(value || "").trim().toUpperCase());
}

function normalizedCustomCurrencyInput(input = elements.pricingReferenceCurrencyCustom) {
  return String(input?.value || "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z]/g, "")
    .slice(0, 3);
}

function customCurrencyInputIsValid(input = elements.pricingReferenceCurrencyCustom) {
  return /^[A-Z]{3}$/.test(normalizedCustomCurrencyInput(input));
}

function setQuoteCurrencyControls(value = DEFAULT_CURRENCY_LABEL) {
  const normalized = normalizeCurrencyLabel(value);
  const isSupported = isStandardCurrencyCode(normalized);
  if (elements.quoteCurrency) {
    elements.quoteCurrency.value = isSupported ? normalized : CUSTOM_CURRENCY_VALUE;
  }
  if (elements.quoteCurrencyCustom) {
    elements.quoteCurrencyCustom.hidden = isSupported;
    elements.quoteCurrencyCustom.required = !isSupported;
    elements.quoteCurrencyCustom.value = isSupported ? "" : normalized;
  }
}

function syncQuoteCurrencyCustomInput() {
  const isCustom = elements.quoteCurrency?.value === CUSTOM_CURRENCY_VALUE;
  if (!elements.quoteCurrencyCustom) return;
  elements.quoteCurrencyCustom.hidden = !isCustom;
  elements.quoteCurrencyCustom.required = isCustom;
  if (!isCustom) elements.quoteCurrencyCustom.value = "";
}

function quoteCurrencyControlValue() {
  const selected = elements.quoteCurrency?.value;
  if (selected === CUSTOM_CURRENCY_VALUE) {
    return customCurrencyInputIsValid(elements.quoteCurrencyCustom)
      ? normalizedCustomCurrencyInput(elements.quoteCurrencyCustom)
      : "";
  }
  return normalizeCurrencyLabel(selected || selectedPricingReferenceCurrency());
}

function setPricingReferenceCurrencyControls(value = DEFAULT_CURRENCY_LABEL) {
  const normalized = normalizeCurrencyLabel(value);
  const isSupported = CURRENCY_OPTIONS.some(([code]) => code === normalized);
  if (elements.pricingReferenceCurrency) {
    elements.pricingReferenceCurrency.value = isSupported ? normalized : CUSTOM_CURRENCY_VALUE;
  }
  if (elements.pricingReferenceCurrencyCustom) {
    elements.pricingReferenceCurrencyCustom.hidden = isSupported;
    elements.pricingReferenceCurrencyCustom.required = !isSupported;
    elements.pricingReferenceCurrencyCustom.value = isSupported ? "" : normalized;
  }
}

function setPricingReferenceTaxControls(tax = {}) {
  if (elements.pricingReferenceTaxLabel) {
    elements.pricingReferenceTaxLabel.value = normalizeTaxLabel(tax.label);
  }
  if (elements.pricingReferenceTaxRate) {
    elements.pricingReferenceTaxRate.value = taxRatePercentText(tax.rate ?? DEFAULT_TAX_RATE);
  }
}

function syncPricingReferenceCurrencyCustomInput() {
  const isCustom = elements.pricingReferenceCurrency?.value === CUSTOM_CURRENCY_VALUE;
  if (!elements.pricingReferenceCurrencyCustom) return;
  elements.pricingReferenceCurrencyCustom.hidden = !isCustom;
  elements.pricingReferenceCurrencyCustom.required = isCustom;
  if (!isCustom) elements.pricingReferenceCurrencyCustom.value = "";
}

const QUOTE_COMMERCIAL_FIELD_KEYS = ["quoteCurrency", "quoteExchangeRate", "quoteTaxLabel", "quoteTaxRate"];

function emptyQuoteCommercialTouched() {
  return QUOTE_COMMERCIAL_FIELD_KEYS.reduce((touched, key) => {
    touched[key] = false;
    return touched;
  }, {});
}

function normalizeQuoteCommercialTouched(touched = {}) {
  const normalized = emptyQuoteCommercialTouched();
  QUOTE_COMMERCIAL_FIELD_KEYS.forEach((key) => {
    normalized[key] = Boolean(touched && touched[key]);
  });
  return normalized;
}

function resetQuoteCommercialTouched() {
  state.quoteCommercialTouched = emptyQuoteCommercialTouched();
}

function quoteCommercialFieldKeyForElement(target = null) {
  if (target && elements.quoteCurrencyCustom && target === elements.quoteCurrencyCustom) return "quoteCurrency";
  return QUOTE_COMMERCIAL_FIELD_KEYS.find((key) => elements[key] && elements[key] === target) || "";
}

function quoteCommercialFieldIsTouched(field = "") {
  const key = typeof field === "string" ? field : quoteCommercialFieldKeyForElement(field);
  const touched = normalizeQuoteCommercialTouched(state.quoteCommercialTouched || {});
  return Boolean(key && touched[key]);
}

function markQuoteCommercialFieldTouched(target = null) {
  const key = quoteCommercialFieldKeyForElement(target);
  if (!key) return false;
  state.quoteCommercialTouched = normalizeQuoteCommercialTouched(state.quoteCommercialTouched || {});
  state.quoteCommercialTouched[key] = true;
  return true;
}

function quoteCommercialFieldHasValue(field = "") {
  const key = typeof field === "string" ? field : quoteCommercialFieldKeyForElement(field);
  if (key === "quoteCurrency" && elements.quoteCurrency?.value === CUSTOM_CURRENCY_VALUE) {
    return customCurrencyInputIsValid(elements.quoteCurrencyCustom);
  }
  return Boolean(key && String(elements[key]?.value || "").trim());
}

function shouldApplyQuoteCommercialField(field, explicit, partial, options = {}) {
  if (options.resetQuoteCommercialTouched === true) return true;
  if (explicit) {
    return partial ? !quoteCommercialFieldIsTouched(field) && !quoteCommercialFieldHasValue(field) : true;
  }
  if (partial) return false;
  return !quoteCommercialFieldIsTouched(field);
}

function quoteDetailsCommercialTouched(details = {}) {
  const quoteText = details.quote_text || {};
  const tax = details.tax || quoteText.tax || {};
  return normalizeQuoteCommercialTouched({
    quoteCurrency: hasOwnValue(details, "currency"),
    quoteExchangeRate: hasOwnValue(details, "exchange_rate"),
    quoteTaxLabel: hasOwnValue(tax, "label") || hasOwnValue(quoteText, "tax_label"),
    quoteTaxRate: hasOwnValue(tax, "rate") || hasOwnValue(quoteText, "tax_rate"),
  });
}

function quoteCommercialOverrideSnapshot() {
  return {
    values: {
      ...QUOTE_COMMERCIAL_FIELD_KEYS.reduce((snapshot, key) => {
        snapshot[key] = String(elements[key]?.value || "");
        return snapshot;
      }, {}),
      quoteCurrencyCustom: String(elements.quoteCurrencyCustom?.value || ""),
    },
    quoteCommercialTouched: normalizeQuoteCommercialTouched(state.quoteCommercialTouched || {}),
  };
}

function quoteCommercialSnapshotHasUserValues(snapshot = {}) {
  const values = snapshot.values && typeof snapshot.values === "object" ? snapshot.values : {};
  const touched = normalizeQuoteCommercialTouched(snapshot.quoteCommercialTouched || {});
  return QUOTE_COMMERCIAL_FIELD_KEYS.some((key) => (
    touched[key] || String(values[key] || "").trim()
  ));
}

function restoreQuoteCommercialOverrideSnapshot(snapshot = {}, options = {}) {
  if (!quoteCommercialSnapshotHasUserValues(snapshot)) return false;
  const values = snapshot.values && typeof snapshot.values === "object" ? snapshot.values : {};
  const touched = normalizeQuoteCommercialTouched(snapshot.quoteCommercialTouched || {});
  state.quoteCommercialTouched = touched;
  QUOTE_COMMERCIAL_FIELD_KEYS.forEach((key) => {
    const value = String(values[key] || "");
    if ((touched[key] || value.trim()) && elements[key]) {
      elements[key].value = value;
    }
  });
  if (elements.quoteCurrencyCustom && String(values.quoteCurrencyCustom || "").trim()) {
    elements.quoteCurrencyCustom.value = normalizedCustomCurrencyInput({ value: values.quoteCurrencyCustom });
  }
  syncQuoteCurrencyCustomInput();
  syncQuoteExchangeRateField();
  syncQuoteCommercialContextPills();
  updateOutputHeader();
  return true;
}

function collectTaxDetails() {
  const referenceTax = selectedPricingReferenceTax();
  const label = elements.quoteTaxLabel?.value || elements.taxLabel?.value || referenceTax.label;
  const rateSource = elements.quoteTaxRate?.value || elements.taxRate?.value;
  return {
    label: normalizeTaxLabel(label),
    rate: rateSource === undefined || String(rateSource || "").trim() === ""
      ? referenceTax.rate
      : taxRateFromPercentInput(rateSource, referenceTax.rate),
  };
}

function collectQuoteCurrency() {
  return normalizeCurrencyLabel(quoteCurrencyControlValue() || selectedPricingReferenceCurrency());
}

function collectQuoteExchangeRate() {
  const value = Number(String(elements.quoteExchangeRate?.value || "").trim());
  if (collectQuoteCurrency() === selectedPricingReferenceCurrency() && !quoteCommercialFieldIsTouched("quoteExchangeRate")) return 1;
  return Number.isFinite(value) && value > 0 ? value : null;
}

function syncQuoteExchangeRateField() {
  if (elements.quoteExchangeRateField) elements.quoteExchangeRateField.hidden = false;
  if (
    elements.quoteExchangeRate
    && collectQuoteCurrency() === selectedPricingReferenceCurrency()
    && !quoteCommercialFieldIsTouched("quoteExchangeRate")
  ) {
    elements.quoteExchangeRate.value = "1";
  }
}

function quoteCommercialTaxText(tax = collectTaxDetails()) {
  return `${tax.label || DEFAULT_TAX_LABEL} ${taxRatePercentText(tax.rate ?? DEFAULT_TAX_RATE)}%`;
}

function quoteExchangeRateText(value = collectQuoteExchangeRate()) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "-";
  return number.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function quoteFxMultiplier(value = collectQuoteExchangeRate()) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 1;
}

function syncQuoteCommercialContextPills() {
  if (typeof document === "undefined") return;
  const currency = collectQuoteCurrency();
  const taxText = quoteCommercialTaxText();
  const exchangeRateText = quoteExchangeRateText();
  document.querySelectorAll("[data-quote-currency]").forEach((element) => {
    element.textContent = currency;
  });
  document.querySelectorAll("[data-quote-tax]").forEach((element) => {
    element.textContent = taxText;
  });
  document.querySelectorAll("[data-quote-exchange-rate]").forEach((element) => {
    element.textContent = exchangeRateText;
  });
}

function applyPricingReferenceCommercialDefaults() {
  const tax = selectedPricingReferenceTax();
  const currency = selectedPricingReferenceCurrency();
  const selectedQuoteCurrency = String(elements.quoteCurrency?.value || "").trim();
  const selectedQuoteTaxLabel = String(elements.quoteTaxLabel?.value || "").trim();
  const quoteCurrencyCanUseReferenceDefault = !selectedQuoteCurrency || selectedQuoteCurrency === DEFAULT_CURRENCY_LABEL;
  const quoteTaxLabelCanUseReferenceDefault = !selectedQuoteTaxLabel || selectedQuoteTaxLabel === DEFAULT_TAX_LABEL;
  if (elements.quoteCurrency && quoteCurrencyCanUseReferenceDefault && !quoteCommercialFieldIsTouched("quoteCurrency")) {
    setQuoteCurrencyControls(currency);
  }
  if (elements.quoteTaxLabel && quoteTaxLabelCanUseReferenceDefault && !quoteCommercialFieldIsTouched("quoteTaxLabel")) {
    elements.quoteTaxLabel.value = tax.label;
  }
  if (elements.quoteTaxRate && !String(elements.quoteTaxRate.value || "").trim() && !quoteCommercialFieldIsTouched("quoteTaxRate")) {
    elements.quoteTaxRate.value = taxRatePercentText(tax.rate);
  }
  syncQuoteExchangeRateField();
}

function resetQuoteCommercialFieldsToSelectedPricingReference() {
  const tax = selectedPricingReferenceTax();
  const currency = selectedPricingReferenceCurrency();
  resetQuoteCommercialTouched();
  if (elements.taxLabel) elements.taxLabel.value = normalizeTaxLabel(tax.label || DEFAULT_TAX_LABEL);
  setInputValue(elements.taxRate, taxRatePercentText(tax.rate ?? DEFAULT_TAX_RATE));
  setQuoteCurrencyControls(currency);
  setInputValue(elements.quoteExchangeRate, "1");
  setInputValue(elements.quoteTaxLabel, normalizeTaxLabel(tax.label || DEFAULT_TAX_LABEL));
  setInputValue(elements.quoteTaxRate, taxRatePercentText(tax.rate ?? DEFAULT_TAX_RATE));
  syncQuoteExchangeRateField();
  syncQuoteCommercialContextPills();
  updateOutputHeader();
}

function renderSelectedPricingReferenceSummary() {
  const reference = currentPricingReference();
  const tax = selectedPricingReferenceTax();
  const currency = selectedPricingReferenceCurrency();
  const taxText = selectedPricingReferenceTaxText();
  if (elements.selectedPricingReferenceSummary) {
    elements.selectedPricingReferenceSummary.textContent = reference
      ? "Managed in Settings."
      : state.pricingReferences.length
        ? "Select a pricing reference."
        : MISSING_PRICING_REFERENCES_MESSAGE;
  }
  if (elements.selectedPricingReferenceCurrency) elements.selectedPricingReferenceCurrency.textContent = currency;
  if (elements.selectedPricingReferenceTax) elements.selectedPricingReferenceTax.textContent = taxText;
  syncPricingReferenceContextPills(currency, taxText);
  applyPricingReferenceCommercialDefaults();
  if (elements.taxLabel) elements.taxLabel.value = tax.label;
  if (elements.taxRate) elements.taxRate.value = taxRatePercentText(tax.rate);
  updatePricingReferenceDeleteButton();
  updateOutputHeader();
}

function todayDateInputValue(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function quoteDateDisplayText(value = "") {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return String(value || "");
  return `${match[3]}/${match[2]}/${match[1]}`;
}

function quoteDateHasFormat() {
  return Boolean(state.quoteDateFormat.bold || state.quoteDateFormat.italic || state.quoteDateFormat.underline);
}

function quoteDateRichTextHtml() {
  const displayText = quoteDateDisplayText(elements.quoteDate.value);
  if (!displayText || !quoteDateHasFormat()) return "";
  let formatted = escapeHtml(displayText);
  if (state.quoteDateFormat.underline) formatted = `<u>${formatted}</u>`;
  if (state.quoteDateFormat.italic) formatted = `<em>${formatted}</em>`;
  if (state.quoteDateFormat.bold) formatted = `<strong>${formatted}</strong>`;
  return `<div>${formatted}</div>`;
}

function applyQuoteDateFormatFromHtml(richHtml = "") {
  const value = String(richHtml || "").toLowerCase();
  state.quoteDateFormat = {
    bold: /<(strong|b)\b/.test(value),
    italic: /<(em|i)\b/.test(value),
    underline: /<u\b/.test(value),
  };
  updateQuoteDateFormatButtons();
}

function updateQuoteDateFormatButtons() {
  elements.quoteDateFormatButtons.forEach((button) => {
    const command = button.dataset.dateFormatCommand || "";
    button.classList.toggle("is-selected", Boolean(state.quoteDateFormat[command]));
    button.setAttribute("aria-pressed", String(Boolean(state.quoteDateFormat[command])));
  });
}

function applyDefaultQuoteDate() {
  if (!elements.quoteDate.value.trim()) {
    setInputValue(elements.quoteDate, todayDateInputValue());
  }
}

function renderFiles() {
  elements.imageIntake.classList.toggle("has-images", Boolean(state.images.length));
  updateGeneratorCopy();
  if (elements.imageInput) elements.imageInput.disabled = state.images.length >= MAX_REFERENCE_IMAGES;
  if (!state.images.length) {
    elements.fileList.innerHTML = "";
    return;
  }
  elements.fileList.innerHTML = state.images
    .map((image, index) => {
      const hasPayload = referenceFileHasPayload(image);
      const thumb = isPdfReference(image)
        ? `<span class="file-thumb file-thumb-file" aria-hidden="true">PDF</span>`
        : hasPayload
          ? `<img class="file-thumb" src="${escapeHtml(image.data_url)}" alt="">`
          : `<span class="file-thumb file-thumb-file" aria-hidden="true">IMG</span>`;
      const payloadNotice = hasPayload ? "" : " - file unavailable";
      return `
        <div class="file-item">
          ${thumb}
          <div>
            <strong>${escapeHtml(image.name)}</strong>
            <span>${escapeHtml(referenceFileTypeLabel(image))} - ${formatBytes(image.size)}${payloadNotice}</span>
          </div>
          <button class="file-remove" type="button" data-remove-image="${index}" aria-label="Remove ${escapeHtml(image.name)}">x</button>
        </div>
      `;
    })
    .join("");
}

function normalizeLineItem(item = {}) {
  const priceMode = item.price_mode === "Included" || String(item.display_price || "").toLowerCase() === "included"
    ? "Included"
    : "Priced";
  const quantityParts = normalizedLineTextQuantityParts(item.description || "", item.quantity ?? "", item.unit || "");
  return {
    section: normalizeCategoryTitle(item.section || ""),
    quantity: quantityParts.quantity ?? "",
    unit: quantityParts.unit || "",
    description: cleanCustomerQuoteLineText(quantityParts.text || ""),
    pricing_keyword: item.pricing_keyword || "",
    display_price: item.display_price || "",
    price_mode: priceMode,
    unit_price_override: item.unit_price_override ?? "",
    catalog_unit_price: item.catalog_unit_price ?? item.unit_price ?? item.sale_unit_price ?? "",
    catalog_description: cleanCustomerQuoteLineText(item.catalog_description || ""),
    pricing_reference_description: pricingReferenceLineText(item.pricing_reference_description || ""),
    source_basis_line_id: item.source_basis_line_id || "",
    category_order: orderNumber(item.category_order) ?? "",
    item_order: orderNumber(item.item_order) ?? "",
    basis_order: orderNumber(item.basis_order) ?? "",
    status: item.status || "",
  };
}

function cloneQuoteBasis(basis = {}) {
  return {
    ...EMPTY_BASIS,
    surfaces: basis.surfaces || "",
    counters: basis.counters || "",
    platform: basis.platform || "",
    graphics: basis.graphics || "",
    furniture: basis.furniture || "",
    electrical: basis.electrical || "",
  };
}

function linesValue(value) {
  return Array.isArray(value) ? value.join("\n") : String(value || "");
}

function hasOwnValue(object, key) {
  return Object.prototype.hasOwnProperty.call(object || {}, key);
}

function hasMeaningfulQuoteDetailValue(value) {
  if (Array.isArray(value)) return value.length > 0;
  if (value && typeof value === "object") return Object.keys(value).length > 0;
  return String(value ?? "").trim().length > 0;
}

function quoteDetailsWithFallbackDefaults(defaults = {}, details = {}) {
  const merge = (base = {}, override = {}) => {
    const merged = { ...(base && typeof base === "object" ? base : {}) };
    Object.entries(override && typeof override === "object" ? override : {}).forEach(([key, value]) => {
      const current = merged[key];
      if (
        value && typeof value === "object" && !Array.isArray(value)
        && current && typeof current === "object" && !Array.isArray(current)
      ) {
        merged[key] = merge(current, value);
        return;
      }
      if (hasMeaningfulQuoteDetailValue(value) || !Object.prototype.hasOwnProperty.call(merged, key)) {
        merged[key] = value;
      }
    });
    return merged;
  };
  return merge(defaults, details);
}

function shouldApply(object, key, partial) {
  return !partial || hasOwnValue(object, key);
}

function richTextEditorFor(input) {
  if (!input) return null;
  return elements.richTextEditors.find((editor) => editor.dataset.richTextSource === input.id) || null;
}

function richTextPlainHtml(value) {
  const lines = normalizeTextNewlines(value).split("\n");
  if (!lines.length || (lines.length === 1 && !lines[0])) return "<div><br></div>";
  return lines.map((line) => `<div>${line ? escapeHtml(line) : "<br>"}</div>`).join("");
}

function richTextEditorPlainText(editor) {
  const blockTags = new Set(["DIV", "P", "LI"]);
  const readNode = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
    if (node.nodeName === "BR") return "\n";
    const isBlock = blockTags.has(node.nodeName);
    const text = Array.from(node.childNodes).map(readNode).join("");
    return isBlock ? `${text}\n` : text;
  };
  return normalizeTextNewlines(Array.from(editor.childNodes).map(readNode).join("")).replace(/\n+$/g, "");
}

function updateRichTextPlaceholderState(editor) {
  if (!editor) return;
  editor.classList.toggle("is-empty", !richTextEditorPlainText(editor).trim());
}

function syncRichTextSource(editor) {
  if (!editor) return;
  const input = qs(`#${editor.dataset.richTextSource}`);
  if (!input) return;
  input.value = richTextEditorPlainText(editor).trimEnd();
  updateRichTextPlaceholderState(editor);
}

function syncRichTextSources() {
  elements.richTextEditors.forEach(syncRichTextSource);
}

function syncRichTextEditor(input, richHtml = "") {
  const editor = richTextEditorFor(input);
  if (!editor || document.activeElement === editor) return;
  if (richHtml) {
    editor.innerHTML = sanitizeRichTextHtml(richHtml);
    updateRichTextPlaceholderState(editor);
    return;
  }
  editor.innerHTML = richTextPlainHtml(input.value ?? "");
  updateRichTextPlaceholderState(editor);
}

function collectRichTextDetails() {
  const details = RICH_TEXT_SOURCE_IDS.reduce((collected, id) => {
    const editor = elements.richTextEditors.find((item) => item.dataset.richTextSource === id);
    if (editor) collected[id] = sanitizeRichTextHtml(editor.innerHTML);
    return collected;
  }, {});
  const quoteDateHtml = quoteDateRichTextHtml();
  if (quoteDateHtml) details.quoteDate = sanitizeRichTextHtml(quoteDateHtml);
  return details;
}

function restoreRichTextDetails(details = {}, options = {}) {
  const partial = Boolean(options.partial);
  RICH_TEXT_SOURCE_IDS.forEach((id) => {
    const input = qs(`#${id}`);
    const editor = elements.richTextEditors.find((item) => item.dataset.richTextSource === id);
    if (!input || !editor) return;
    if (details[id]) {
      editor.innerHTML = sanitizeRichTextHtml(details[id]);
    } else if (!partial) {
      editor.innerHTML = richTextPlainHtml(input.value ?? "");
    } else {
      return;
    }
    syncRichTextSource(editor);
  });
  if (!partial || hasOwnValue(details, "quoteDate")) {
    applyQuoteDateFormatFromHtml(details.quoteDate || "");
  }
}

function hasEditableSelection(editor) {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return false;
  const range = selection.getRangeAt(0);
  return editor.contains(range.commonAncestorContainer);
}

function runRichTextCommand(editor, command) {
  if (!hasEditableSelection(editor)) return;
  editor.focus();
  if (command === "bold") {
    document.execCommand("bold", false, null);
  } else if (command === "italic") {
    document.execCommand("italic", false, null);
  } else {
    document.execCommand("underline", false, null);
  }
  syncRichTextSource(editor);
  syncControlStates();
}

function wireRichTextEditors() {
  elements.richTextEditors.forEach((editor) => {
    syncRichTextEditor(qs(`#${editor.dataset.richTextSource}`));
    editor.addEventListener("input", () => {
      syncRichTextSource(editor);
      syncControlStates();
    });
    editor.addEventListener("keydown", (event) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (!["b", "i", "u"].includes(key)) return;
      event.preventDefault();
      const command = key === "b" ? "bold" : key === "i" ? "italic" : "underline";
      runRichTextCommand(editor, command);
    });
  });
  elements.richTextToolbar.forEach((button) => {
    button.addEventListener("pointerdown", (event) => {
      event.preventDefault();
    });
    button.addEventListener("click", (event) => {
      if (event.detail > 1) return;
      const field = button.closest(".rich-text-field");
      const editor = field?.querySelector("[data-rich-text-source]");
      if (editor) runRichTextCommand(editor, button.dataset.richCommand || "bold");
    });
  });
  elements.quoteDateFormatButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const command = button.dataset.dateFormatCommand || "";
      if (!Object.prototype.hasOwnProperty.call(state.quoteDateFormat, command)) return;
      state.quoteDateFormat[command] = !state.quoteDateFormat[command];
      updateQuoteDateFormatButtons();
      syncControlStates();
    });
  });
  updateQuoteDateFormatButtons();
}

function setInputValue(input, value) {
  if (!input) return;
  input.value = value ?? "";
  syncRichTextEditor(input);
}

function normalizeBoothDimensions(project = {}) {
  const width = Number(project.booth_width);
  const depth = Number(project.booth_depth);
  if (Number.isFinite(width) && Number.isFinite(depth) && width > 0 && depth > 0) {
    const formattedWidth = Number.isInteger(width) ? String(width) : String(width);
    const formattedDepth = Number.isInteger(depth) ? String(depth) : String(depth);
    return {
      booth_width: formattedWidth,
      booth_depth: formattedDepth,
      booth_size: project.booth_size || `${formattedWidth}m x ${formattedDepth}m`,
      dimension_source: project.dimension_source || "analysis",
    };
  }
  return { ...DEFAULT_BOOTH_DIMENSIONS };
}

function collectQuoteDetails() {
  syncRichTextSources();
  return {
    quote_date: elements.quoteDate.value,
    project_number: elements.projectNumber.value.trim(),
    client: {
      name: elements.clientName.value.trim(),
      attention: elements.clientAttention.value.trim(),
      title: elements.clientTitle.value.trim(),
      address: elements.clientAddress.value,
    },
    project: {
      title: elements.projectTitle.value.trim(),
      show_name: elements.showName?.value.trim() || "",
    },
    company: {
      name: elements.quoteCompanyName.value.trim(),
      header_details: elements.headerDetails.value,
      logo_data_url: state.headerLogo ? state.headerLogo.data_url : "",
      logo_name: state.headerLogo ? state.headerLogo.name : "",
      logo_type: state.headerLogo ? state.headerLogo.type : "",
    },
    currency: collectQuoteCurrency(),
    exchange_rate: collectQuoteExchangeRate(),
    tax: collectTaxDetails(),
    quote_text: {
      terms_heading: elements.termsHeading.value.trim(),
      payment_terms: splitLines(elements.paymentTerms.value),
      notes_heading: elements.notesHeading.value.trim(),
      standard_notes: splitLines(elements.standardNotes.value),
      acceptance_text: elements.acceptanceText.value.trim(),
      person_label: elements.personLabel.value.trim(),
      stamp_label: elements.stampLabel.value.trim(),
      date_label: elements.dateLabel.value.trim(),
    },
    signature: {
      company_signatory: elements.companySignatory.value.trim(),
      company_title: elements.companyTitle.value.trim(),
      company_date_label: elements.companyDateLabel.value.trim(),
    },
    rich_text: collectRichTextDetails(),
  };
}

function collectQuoteCompanyProfileDetails() {
  const details = collectQuoteDetails();
  const richText = details.rich_text || {};
  return {
    company: details.company || {},
    quote_text: details.quote_text || {},
    signature: details.signature || {},
    rich_text: QUOTE_COMPANY_RICH_TEXT_IDS.reduce((collected, id) => {
      if (Object.prototype.hasOwnProperty.call(richText, id)) collected[id] = richText[id];
      return collected;
    }, {}),
  };
}

function applyQuoteDetails(details = {}, options = {}) {
  const client = details.client || {};
  const project = details.project || {};
  const company = details.company || {};
  const quoteText = details.quote_text || {};
  const signature = details.signature || {};
  const tax = details.tax || quoteText.tax || {};
  const partial = Boolean(options.partial);
  const hasQuoteTaxLabel = hasOwnValue(details, "tax") || hasOwnValue(quoteText, "tax") || hasOwnValue(quoteText, "tax_label");
  const hasQuoteTaxRate = hasOwnValue(details, "tax") || hasOwnValue(quoteText, "tax") || hasOwnValue(quoteText, "tax_rate");

  if (shouldApply(client, "name", partial)) setInputValue(elements.clientName, client.name);
  if (shouldApply(client, "attention", partial)) setInputValue(elements.clientAttention, client.attention);
  if (shouldApply(client, "title", partial)) setInputValue(elements.clientTitle, client.title);
  if (shouldApply(client, "address", partial)) setInputValue(elements.clientAddress, client.address);
  if (shouldApply(project, "title", partial)) setInputValue(elements.projectTitle, project.title);
  if (shouldApply(project, "show_name", partial)) setInputValue(elements.showName, project.show_name);
  if (!partial || shouldApply(project, "booth_width", true) || shouldApply(project, "booth_depth", true)) {
    state.boothDimensions = normalizeBoothDimensions(project);
  }
  if (shouldApply(details, "quote_date", partial)) setInputValue(elements.quoteDate, details.quote_date);
  if (shouldApply(details, "project_number", partial)) setInputValue(elements.projectNumber, details.project_number);
  if (shouldApply(company, "name", partial)) setInputValue(elements.quoteCompanyName, company.name);
  if (shouldApply(company, "header_details", partial)) setInputValue(elements.headerDetails, company.header_details);
  if (!partial || hasQuoteTaxLabel) {
    if (elements.taxLabel) elements.taxLabel.value = normalizeTaxLabel(tax.label || quoteText.tax_label || DEFAULT_TAX_LABEL);
    if (
      elements.quoteTaxLabel
      && shouldApplyQuoteCommercialField("quoteTaxLabel", hasQuoteTaxLabel, partial, options)
    ) {
      elements.quoteTaxLabel.value = normalizeTaxLabel(tax.label || quoteText.tax_label || DEFAULT_TAX_LABEL);
    }
  }
  if (!partial || hasQuoteTaxRate) {
    setInputValue(elements.taxRate, taxRatePercentText(tax.rate ?? quoteText.tax_rate ?? DEFAULT_TAX_RATE));
    if (
      elements.quoteTaxRate
      && shouldApplyQuoteCommercialField("quoteTaxRate", hasQuoteTaxRate, partial, options)
    ) {
      setInputValue(elements.quoteTaxRate, taxRatePercentText(tax.rate ?? quoteText.tax_rate ?? DEFAULT_TAX_RATE));
    }
  }
  if (shouldApplyQuoteCommercialField("quoteCurrency", hasOwnValue(details, "currency"), partial, options)) {
    setQuoteCurrencyControls(normalizeCurrencyLabel(details.currency || selectedPricingReferenceCurrency()));
  }
  if (shouldApplyQuoteCommercialField("quoteExchangeRate", hasOwnValue(details, "exchange_rate"), partial, options)) {
    setInputValue(elements.quoteExchangeRate, quoteExchangeRateText(details.exchange_rate ?? 1));
  }
  syncQuoteExchangeRateField();
  syncQuoteCommercialContextPills();
  if (shouldApply(quoteText, "terms_heading", partial)) setInputValue(elements.termsHeading, quoteText.terms_heading);
  if (shouldApply(quoteText, "payment_terms", partial)) setInputValue(elements.paymentTerms, linesValue(quoteText.payment_terms));
  if (shouldApply(quoteText, "notes_heading", partial)) setInputValue(elements.notesHeading, quoteText.notes_heading);
  if (shouldApply(quoteText, "standard_notes", partial)) setInputValue(elements.standardNotes, linesValue(quoteText.standard_notes));
  if (shouldApply(quoteText, "acceptance_text", partial)) setInputValue(elements.acceptanceText, quoteText.acceptance_text);
  if (shouldApply(signature, "company_signatory", partial)) setInputValue(elements.companySignatory, signature.company_signatory);
  if (shouldApply(signature, "company_title", partial)) setInputValue(elements.companyTitle, signature.company_title);
  if (shouldApply(signature, "company_date_label", partial)) setInputValue(elements.companyDateLabel, signature.company_date_label);
  if (shouldApply(quoteText, "person_label", partial)) setInputValue(elements.personLabel, quoteText.person_label);
  if (shouldApply(quoteText, "stamp_label", partial)) setInputValue(elements.stampLabel, quoteText.stamp_label);
  if (shouldApply(quoteText, "date_label", partial)) setInputValue(elements.dateLabel, quoteText.date_label);
  if (options.includeLogo && company.logo_data_url) {
    state.headerLogo = {
      name: company.logo_name || "preset-logo",
      type: company.logo_type || "image/png",
      size: 0,
      data_url: company.logo_data_url,
    };
  } else if (options.clearLogo) {
    state.headerLogo = null;
  }
  const hasRichText = hasOwnValue(details, "rich_text") && details.rich_text && typeof details.rich_text === "object";
  if (hasRichText || !partial) {
    restoreRichTextDetails(hasRichText ? details.rich_text : {}, { partial: partial && hasRichText });
  }
  applyDefaultQuoteDate();
  renderHeaderLogoPreview();
  renderPresetStatus();
}

function applyDefaultQuoteCompanyFields() {
  resetQuoteCommercialFieldsToSelectedPricingReference();
  setInputValue(elements.termsHeading, DEFAULT_TERMS_HEADING);
  setInputValue(elements.notesHeading, DEFAULT_NOTES_HEADING);
  setInputValue(elements.acceptanceText, DEFAULT_ACCEPTANCE_TEXT);
  setInputValue(elements.companyDateLabel, DEFAULT_DATE_LABEL);
  setInputValue(elements.personLabel, DEFAULT_PERSON_LABEL);
  setInputValue(elements.stampLabel, DEFAULT_STAMP_LABEL);
  setInputValue(elements.dateLabel, DEFAULT_DATE_LABEL);
  restoreRichTextDetails(DEFAULT_QUOTE_COMPANY_RICH_TEXT, { partial: true });
}

function safeSessionJson() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(QUOTE_SESSION_STORAGE_KEY) || "null");
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function buildWorkspaceViewSnapshot() {
  return {
    version: 1,
    browserRecoveryScope: currentBrowserRecoveryScope(),
    savedAt: new Date().toISOString(),
    activeAppView: state.activeAppView === "quote" ? "quote" : "dashboard",
    activeSidePanel: SIDE_PANEL_SEQUENCE.includes(state.activeSidePanel) ? state.activeSidePanel : "images",
    restorableOverlay: normalizeRestorableOverlay(state.restorableOverlay),
    pricingReferenceSettingsMode: normalizePricingReferenceSettingsMode(state.pricingReferenceSettingsMode),
    pendingAnalysisMode: normalizeAnalysisMode(state.pendingAnalysisMode),
    dashboardStatusFilter: String(state.dashboardStatusFilter || "all"),
    dashboardDateFilter: String(state.dashboardDateFilter || "all"),
    dashboardCustomDateStart: String(state.dashboardCustomDateStart || ""),
    dashboardCustomDateEnd: String(state.dashboardCustomDateEnd || ""),
    dashboardSortMode: String(state.dashboardSortMode || "created"),
    dashboardSearch: String(state.dashboardSearch || ""),
    dashboardPageSize: Number(state.dashboardPageSize),
    dashboardPageIndex: Number(state.dashboardPageIndex),
    dashboardSelectionMode: Boolean(state.dashboardSelectionMode),
    dashboardSelectedSessionIds: (state.dashboardSelectedSessionIds || []).map(safeQuoteSessionId).filter(Boolean),
    dashboardActiveSessionId: safeQuoteSessionId(state.dashboardActiveSessionId || ""),
  };
}

function safeWorkspaceViewJson() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(WORKSPACE_VIEW_STORAGE_KEY) || "null");
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function saveWorkspaceViewState() {
  if (state.isBooting || !currentBrowserRecoveryScope()) return;
  try {
    window.localStorage.setItem(WORKSPACE_VIEW_STORAGE_KEY, JSON.stringify(buildWorkspaceViewSnapshot()));
  } catch {
    // Exact screen recovery is best-effort; quote data remains in its separate recovery record.
  }
}

function clearWorkspaceViewState() {
  try {
    window.localStorage.removeItem(WORKSPACE_VIEW_STORAGE_KEY);
  } catch {
    // Ignore storage failures during logout or recovery-scope changes.
  }
}

function restoredWorkspaceViewState() {
  const saved = safeWorkspaceViewJson();
  if (!saved || saved.version !== 1 || saved.browserRecoveryScope !== currentBrowserRecoveryScope()) {
    clearWorkspaceViewState();
    return null;
  }
  return saved;
}

function applyWorkspaceViewState(saved = {}, options = {}) {
  if (!saved || typeof saved !== "object") return false;
  const quoteRestored = options.quoteRestored === true;
  state.activeAppView = saved.activeAppView === "quote" && quoteRestored ? "quote" : "dashboard";
  if (state.activeAppView === "quote" && SIDE_PANEL_SEQUENCE.includes(saved.activeSidePanel)) {
    setSidePanel(saved.activeSidePanel, { force: true });
  }
  const overlay = normalizeRestorableOverlay(saved.restorableOverlay);
  state.restorableOverlay = quoteRestored || overlay === "pricing_reference_settings" ? overlay : "";
  state.pricingReferenceSettingsMode = normalizePricingReferenceSettingsMode(saved.pricingReferenceSettingsMode);
  state.pendingAnalysisMode = normalizeAnalysisMode(saved.pendingAnalysisMode);
  state.dashboardStatusFilter = String(saved.dashboardStatusFilter || "all");
  state.dashboardDateFilter = String(saved.dashboardDateFilter || "all");
  state.dashboardCustomDateStart = String(saved.dashboardCustomDateStart || "");
  state.dashboardCustomDateEnd = String(saved.dashboardCustomDateEnd || "");
  state.dashboardSortMode = String(saved.dashboardSortMode || "created");
  state.dashboardSearch = String(saved.dashboardSearch || "");
  state.dashboardPageSize = DASHBOARD_PAGE_SIZE_OPTIONS.includes(Number(saved.dashboardPageSize))
    ? Number(saved.dashboardPageSize)
    : DASHBOARD_DEFAULT_PAGE_SIZE;
  state.dashboardPageIndex = Math.max(0, Number(saved.dashboardPageIndex) || 0);
  state.dashboardSelectionMode = Boolean(saved.dashboardSelectionMode);
  state.dashboardSelectedSessionIds = Array.isArray(saved.dashboardSelectedSessionIds)
    ? saved.dashboardSelectedSessionIds.map(safeQuoteSessionId).filter(Boolean)
    : [];
  state.dashboardActiveSessionId = safeQuoteSessionId(saved.dashboardActiveSessionId || "");
  return true;
}

function openSessionFileDb() {
  if (!window.indexedDB) return Promise.reject(new Error("IndexedDB unavailable"));
  if (sessionFileDbPromise) return sessionFileDbPromise;
  sessionFileDbPromise = new Promise((resolve, reject) => {
    const request = window.indexedDB.open(QUOTE_SESSION_FILE_DB_NAME, QUOTE_SESSION_FILE_DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(QUOTE_SESSION_FILE_STORE_NAME)) {
        db.createObjectStore(QUOTE_SESSION_FILE_STORE_NAME, { keyPath: "session_file_key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("Could not open session file store"));
    request.onblocked = () => reject(new Error("Session file store is blocked"));
  }).catch((error) => {
    sessionFileDbPromise = null;
    throw error;
  });
  return sessionFileDbPromise;
}

function sessionFileKeyForImage(image = {}, index = 0) {
  const existing = String(image.session_file_key || "").trim();
  if (existing) return existing;
  const namePart = String(image.name || "reference-file")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "reference-file";
  const sizePart = String(Number.isFinite(Number(image.size)) ? Number(image.size) : 0);
  const key = `${Date.now().toString(36)}-${index}-${sizePart}-${namePart}-${Math.random().toString(36).slice(2, 10)}`;
  image.session_file_key = key;
  return key;
}

function sessionFileKeyForLogo(logo = state.headerLogo) {
  const existing = String(logo?.session_file_key || "").trim();
  if (existing) return existing;
  if (!logo || typeof logo !== "object") return "";
  const namePart = String(logo.name || "header-logo")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "header-logo";
  const sizePart = String(Number.isFinite(Number(logo.size)) ? Number(logo.size) : 0);
  const key = `${Date.now().toString(36)}-logo-${sizePart}-${namePart}-${Math.random().toString(36).slice(2, 10)}`;
  logo.session_file_key = key;
  return key;
}

function sessionImageMetadata(image = {}, index = 0) {
  const sessionFileKey = sessionFileKeyForImage(image, index);
  return {
    name: String(image.name || `reference-${index + 1}`).trim() || `reference-${index + 1}`,
    type: referenceFileType(image),
    size: Number.isFinite(Number(image.size)) ? Number(image.size) : 0,
    session_file_key: sessionFileKey,
  };
}

function quoteDetailsWithSessionLogoMetadata(details = collectQuoteDetails()) {
  const company = { ...(details.company || {}) };
  const logo = state.headerLogo?.data_url ? state.headerLogo : null;
  const logoDataUrl = String(logo?.data_url || company.logo_data_url || "").trim();
  if (!logoDataUrl) return { ...details, company };
  const logoSource = logo || {
    name: company.logo_name || "header-logo",
    type: company.logo_type || "image/png",
    size: Number.isFinite(Number(company.logo_size)) ? Number(company.logo_size) : 0,
    data_url: logoDataUrl,
    session_file_key: company.logo_session_file_key || "",
  };
  const sessionFileKey = sessionFileKeyForLogo(logoSource);
  company.logo_name = logoSource.name || company.logo_name || "header-logo";
  company.logo_type = logoSource.type || company.logo_type || "image/png";
  company.logo_size = Number.isFinite(Number(logoSource.size)) ? Number(logoSource.size) : 0;
  company.logo_session_file_key = sessionFileKey;
  delete company.logo_data_url;
  return { ...details, company };
}

function sessionFileRecordsFromImages(images = state.images) {
  return images
    .slice(0, MAX_REFERENCE_IMAGES)
    .map((image, index) => ({
      ...sessionImageMetadata(image, index),
      file_role: "reference",
      data_url: String(image.data_url || "").trim(),
    }))
    .filter((record) => record.session_file_key && record.data_url);
}

function sessionFileRecordFromHeaderLogo(logo = state.headerLogo) {
  const dataUrl = String(logo?.data_url || "").trim();
  if (!dataUrl) return null;
  const sessionFileKey = sessionFileKeyForLogo(logo);
  return {
    name: String(logo.name || "header-logo").trim() || "header-logo",
    type: String(logo.type || "image/png").trim() || "image/png",
    size: Number.isFinite(Number(logo.size)) ? Number(logo.size) : 0,
    session_file_key: sessionFileKey,
    file_role: "quote_company_logo",
    data_url: dataUrl,
  };
}

function sessionFileRecordsFromDraft() {
  return [
    ...sessionFileRecordsFromImages(),
    sessionFileRecordFromHeaderLogo(),
  ].filter(Boolean);
}

function persistSessionFiles(records = []) {
  const currentScope = currentBrowserRecoveryScope();
  if (!currentScope) return Promise.resolve();
  const validRecords = records
    .filter((record) => record?.session_file_key && record?.data_url)
    .map((record) => ({ ...record, browserRecoveryScope: currentScope }));
  if (!validRecords.length) return Promise.resolve();
  return openSessionFileDb().then((db) => new Promise((resolve, reject) => {
    const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readwrite");
    const store = transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME);
    validRecords.forEach((record) => store.put(record));
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("Could not persist session files"));
    transaction.onabort = () => reject(transaction.error || new Error("Session file persistence aborted"));
  }));
}

function loadSessionFileMap(keys = []) {
  const currentScope = currentBrowserRecoveryScope();
  const uniqueKeys = [...new Set(keys.map((key) => String(key || "").trim()).filter(Boolean))];
  if (!currentScope || !uniqueKeys.length) return Promise.resolve(new Map());
  return openSessionFileDb().then((db) => new Promise((resolve, reject) => {
    const found = new Map();
    const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readonly");
    const store = transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME);
    uniqueKeys.forEach((key) => {
      const request = store.get(key);
      request.onsuccess = () => {
        if (request.result?.data_url && request.result.browserRecoveryScope === currentScope) {
          found.set(key, request.result);
        }
      };
    });
    transaction.oncomplete = () => resolve(found);
    transaction.onerror = () => reject(transaction.error || new Error("Could not load session files"));
    transaction.onabort = () => reject(transaction.error || new Error("Session file loading aborted"));
  }));
}

function clearSessionFiles() {
  return openSessionFileDb().then((db) => new Promise((resolve, reject) => {
    const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readwrite");
    transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME).clear();
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error("Could not clear session files"));
    transaction.onabort = () => reject(transaction.error || new Error("Session file clearing aborted"));
  }));
}

function buildSessionSnapshot() {
  return {
    version: QUOTE_SESSION_STATE_VERSION,
    browserRecoveryScope: currentBrowserRecoveryScope(),
    savedAt: new Date().toISOString(),
    activeAppView: state.activeAppView,
    restorableOverlay: normalizeRestorableOverlay(state.restorableOverlay),
    pricingReferenceSettingsMode: state.pricingReferenceSettingsMode,
    profileId: state.profileId,
    pricingReferenceId: state.pricingReferenceId,
    pricingReferenceSource: state.pricingReferenceSource,
    selectedPresetValue: state.selectedPresetValue,
    quoteSessionId: state.quoteSessionId,
    quoteSessionDraftSaveStarted: state.quoteSessionDraftSaveStarted,
    quoteSessionRestoredSessionId: state.quoteSessionRestoredSessionId,
    quoteSessionRestoredDraftKey: state.quoteSessionRestoredDraftKey,
    quoteCommercialTouched: normalizeQuoteCommercialTouched(state.quoteCommercialTouched || {}),
    images: state.images.slice(0, MAX_REFERENCE_IMAGES).map(sessionImageMetadata),
    quoteDetails: quoteDetailsWithSessionLogoMetadata(collectQuoteDetails()),
    workflowStage: state.workflowStage,
    quoteBasis: state.quoteBasis,
    quoteBasisSections: state.quoteBasisSections,
    lineItems: state.lineItems,
    outputRows: state.outputRows,
    originalOutputRows: state.originalOutputRows,
    outputErrors: state.outputErrors,
    outputSortMode: state.outputSortMode,
    analysisFindings: state.analysisFindings,
    blockingClarificationQuestions: state.blockingClarificationQuestions,
    boothDimensions: state.boothDimensions,
    originalAnalysisSnapshot: state.originalAnalysisSnapshot,
    basisConfirmed: state.basisConfirmed,
    aiFailed: state.aiFailed,
    draftSource: state.draftSource,
    lastAnalysisMode: state.lastAnalysisMode,
    pendingAnalysisMode: state.pendingAnalysisMode,
    pendingFeedback: state.pendingFeedback,
    basisChat: state.basisChat,
    activeSidePanel: state.activeSidePanel,
    downloadFile: state.downloadFile,
    pdfFile: state.pdfFile,
    outputRevision: state.outputRevision,
    downloadFileRevision: state.downloadFileRevision,
    pdfFileRevision: state.pdfFileRevision,
    pricingMatches: state.pricingMatches,
    pricingIssues: state.pricingIssues,
    activeJob: normalizeActiveJob(state.activeJob || {}),
  };
}

function clearSessionState() {
  try {
    window.localStorage.removeItem(QUOTE_SESSION_STORAGE_KEY);
  } catch {
    // Ignore storage failures; the local workflow can continue without refresh recovery.
  }
  clearSessionFiles().catch(() => {});
}

async function purgeBrowserRecoveryState() {
  try {
    window.localStorage.removeItem(QUOTE_SESSION_STORAGE_KEY);
  } catch {
    // Ignore storage failures; scope mismatches still remain blocked from restoration.
  }
  clearWorkspaceViewState();
  clearLastSelectionState();
  await clearSessionFiles().catch(() => {});
}

function saveSessionState() {
  if (state.isBooting || !currentBrowserRecoveryScope()) return;
  try {
    if (state.activeAppView === "dashboard" && !safeQuoteSessionId(state.quoteSessionId) && !quoteDraftShouldPersistToDashboard()) {
      window.localStorage.removeItem(QUOTE_SESSION_STORAGE_KEY);
      saveWorkspaceViewState();
      return;
    }
    const fileRecords = sessionFileRecordsFromDraft();
    const snapshot = buildSessionSnapshot();
    window.localStorage.setItem(QUOTE_SESSION_STORAGE_KEY, JSON.stringify(snapshot));
    saveWorkspaceViewState();
    persistSessionFiles(fileRecords).catch(() => {});
  } catch {
    // Refresh recovery is best-effort; the local workflow can continue without persisted state.
  }
}

async function restoreSessionImages(savedImages = []) {
  const images = Array.isArray(savedImages) ? savedImages.slice(0, MAX_REFERENCE_IMAGES) : [];
  const keys = images
    .filter((image) => !String(image?.data_url || "").trim())
    .map((image) => image?.session_file_key);
  const fileMap = await loadSessionFileMap(keys).catch(() => new Map());
  return images
    .map((image) => {
      const key = String(image?.session_file_key || "").trim();
      const stored = key ? fileMap.get(key) : null;
      return {
        ...image,
        ...(stored || {}),
        type: stored?.type || image?.type || referenceFileType(stored || image),
        size: Number.isFinite(Number(stored?.size ?? image?.size)) ? Number(stored?.size ?? image?.size) : 0,
      };
    })
    .filter((image) => (
      referenceFileHasPayload(image)
      || String(image.session_file_key || image.name || "").trim()
    ));
}

function selectedPresetCompanyLogo() {
  const preset = selectedPreset();
  const details = preset?.details && typeof preset.details === "object" ? preset.details : {};
  const company = details.company && typeof details.company === "object" ? details.company : {};
  return String(company.logo_data_url || "").trim() ? company : null;
}

async function restoreQuoteDetailsLogo(details = {}) {
  const company = details.company && typeof details.company === "object" ? details.company : {};
  if (String(company.logo_data_url || "").trim()) return details;
  const sessionFileKey = String(company.logo_session_file_key || "").trim();
  let restoredLogo = null;
  if (sessionFileKey) {
    const fileMap = await loadSessionFileMap([sessionFileKey]).catch(() => new Map());
    restoredLogo = fileMap.get(sessionFileKey) || null;
  }
  const presetLogo = restoredLogo ? null : selectedPresetCompanyLogo();
  const source = restoredLogo || presetLogo;
  if (!source?.data_url && !source?.logo_data_url) return details;
  return {
    ...details,
    company: {
      ...company,
      logo_data_url: source.data_url || source.logo_data_url,
      logo_name: source.name || source.logo_name || company.logo_name || "header-logo",
      logo_type: source.type || source.logo_type || company.logo_type || "image/png",
    },
  };
}

function quoteOutputProgressForNavigation() {
  return Boolean(
    state.outputRows.length
    || state.originalOutputRows.length
    || state.downloadFile
    || state.pdfFile
  );
}

function restoredWorkflowStage(saved = {}) {
  const hasRestoredProgress = Boolean(
    state.lineItems.length
    || state.outputRows.length
    || state.quoteBasisSections.length
    || Object.values(state.quoteBasis || {}).some((value) => splitLines(value).length > 0)
  );
  const savedStage = String(saved.workflowStage || "").trim();
  if (quoteOutputProgressForNavigation() || state.basisConfirmed) {
    return ["generating", "pricing_review", "completed"].includes(savedStage) ? savedStage : "pricing_review";
  }
  if (savedStage && (state.images.length || hasRestoredProgress)) return savedStage;
  if (hasRestoredProgress) return "basis_review";
  if (state.images.length) return "ready_to_analyze";
  return "needs_images";
}

function furthestQuoteSessionSidePanel(saved = {}) {
  const activePanel = SIDE_PANEL_SEQUENCE.includes(saved.activeSidePanel) ? saved.activeSidePanel : "images";
  let furthestIndex = SIDE_PANEL_SEQUENCE.indexOf(activePanel);
  const workflowStage = String(saved.workflowStage || "").trim();
  const quoteBasis = saved.quoteBasis && typeof saved.quoteBasis === "object" ? saved.quoteBasis : {};
  const hasBasisText = Object.values(quoteBasis).some((value) => splitLines(value).length > 0);
  const hasOutput = Boolean(
    (Array.isArray(saved.outputRows) && saved.outputRows.length)
    || (Array.isArray(saved.originalOutputRows) && saved.originalOutputRows.length)
    || saved.downloadFile
    || saved.pdfFile
    || saved.basisConfirmed
    || ["completed", "pricing_review", "generating"].includes(workflowStage)
  );
  const hasBasis = Boolean(
    hasOutput
    || (Array.isArray(saved.lineItems) && saved.lineItems.length)
    || (Array.isArray(saved.quoteBasisSections) && saved.quoteBasisSections.length)
    || (Array.isArray(saved.analysisFindings) && saved.analysisFindings.length)
    || hasBasisText
  );
  if (hasOutput) furthestIndex = Math.max(furthestIndex, SIDE_PANEL_SEQUENCE.indexOf("output"));
  else if (hasBasis) furthestIndex = Math.max(furthestIndex, SIDE_PANEL_SEQUENCE.indexOf("basis"));
  return SIDE_PANEL_SEQUENCE[Math.max(0, furthestIndex)] || "images";
}

function restoredQuoteSessionSidePanel(saved = {}, options = {}) {
  if (options.restoreFurthestPanel === true) return furthestQuoteSessionSidePanel(saved);
  return SIDE_PANEL_SEQUENCE.includes(saved.activeSidePanel)
    ? saved.activeSidePanel
    : furthestQuoteSessionSidePanel(saved);
}

async function applyQuoteSessionSnapshot(saved = {}, options = {}) {
  if (!saved || typeof saved !== "object" || saved.version !== QUOTE_SESSION_STATE_VERSION) {
    return false;
  }
  state.profileId = saved.profileId || "";
  state.pricingReferenceId = saved.pricingReferenceId || saved.profileId || "";
  state.pricingReferenceSource = saved.pricingReferenceSource || "";
  state.quoteSessionId = safeQuoteSessionId(options.sessionId || saved.quoteSessionId || "");
  state.quoteSessionDraftSaveStarted = Boolean(saved.quoteSessionDraftSaveStarted || options.sessionId);
  const restoredSessionId = safeQuoteSessionId(saved.quoteSessionRestoredSessionId || "");
  state.quoteSessionRestoredSessionId = restoredSessionId && restoredSessionId === state.quoteSessionId ? restoredSessionId : "";
  state.quoteSessionRestoredDraftKey = state.quoteSessionRestoredSessionId ? String(saved.quoteSessionRestoredDraftKey || "") : "";
  state.activeAppView = saved.activeAppView === "quote" || options.forceQuoteView ? "quote" : "dashboard";
  state.restorableOverlay = normalizeRestorableOverlay(saved.restorableOverlay);
  state.pricingReferenceSettingsMode = state.restorableOverlay
    ? normalizePricingReferenceSettingsMode(saved.pricingReferenceSettingsMode)
    : PRICING_REFERENCE_SETTINGS_MODE_MANAGE;
  state.selectedPresetValue = saved.selectedPresetValue || presetValueFromQuoteDetails(saved.quoteDetails || {}) || lastSelectedPresetValue();
  state.quoteCommercialTouched = normalizeQuoteCommercialTouched(
    saved.quoteCommercialTouched || quoteDetailsCommercialTouched(saved.quoteDetails || {})
  );
  syncSelectedPricingReference();
  renderProfileOptions();
  renderPresetOptions();
  state.pendingFeedback = String(saved.pendingFeedback || "");
  const restoredQuoteDetails = await restoreQuoteDetailsLogo(
    quoteDetailsWithFallbackDefaults(selectedPreset()?.details || {}, saved.quoteDetails || {})
  );
  applyQuoteDetails(restoredQuoteDetails, { includeLogo: true, clearLogo: true });
  state.images = await restoreSessionImages(saved.images);
  state.quoteBasis = cloneQuoteBasis(saved.quoteBasis || {});
  state.quoteBasisSections = normalizeQuoteBasisSections(saved.quoteBasisSections || saved.quoteBasis || {});
  state.lineItems = Array.isArray(saved.lineItems) ? saved.lineItems.map(normalizeLineItem) : [];
  state.outputRows = Array.isArray(saved.outputRows) ? saved.outputRows.map(normalizeOutputRow) : [];
  state.originalOutputRows = Array.isArray(saved.originalOutputRows) ? saved.originalOutputRows.map(normalizeOutputRow) : [];
  state.outputErrors = Array.isArray(saved.outputErrors) ? saved.outputErrors : [];
  state.outputSortMode = "pricing_reference";
  state.analysisFindings = Array.isArray(saved.analysisFindings) ? saved.analysisFindings : [];
  state.blockingClarificationQuestions = Array.isArray(saved.blockingClarificationQuestions) ? saved.blockingClarificationQuestions : [];
  state.boothDimensions = normalizeBoothDimensions(saved.boothDimensions || saved.quoteDetails?.project || {});
  state.originalAnalysisSnapshot = saved.originalAnalysisSnapshot || null;
  state.basisConfirmed = Boolean(saved.basisConfirmed);
  state.draftSource = saved.draftSource || "";
  state.lastAnalysisMode = normalizeAnalysisMode(saved.lastAnalysisMode || saved.originalAnalysisSnapshot?.analysis_mode);
  state.pendingAnalysisMode = normalizeAnalysisMode(saved.pendingAnalysisMode || state.lastAnalysisMode);
  const savedBasisChat = saved.basisChat && typeof saved.basisChat === "object" ? saved.basisChat : {};
  state.basisChat = {
    ...state.basisChat,
    ...savedBasisChat,
    lineIndex: Number.isInteger(Number(savedBasisChat.lineIndex)) ? Number(savedBasisChat.lineIndex) : -1,
    proposal: savedBasisChat.proposal && typeof savedBasisChat.proposal === "object" ? savedBasisChat.proposal : null,
  };
  state.aiFailed = Boolean(saved.aiFailed || state.draftSource === "local");
  state.downloadFile = saved.downloadFile || null;
  state.pdfFile = saved.pdfFile || null;
  state.outputRevision = revisionNumber(saved.outputRevision, 0);
  state.downloadFileRevision = revisionNumber(
    saved.downloadFileRevision ?? saved.downloadFile?.output_revision,
    -1
  );
  state.pdfFileRevision = revisionNumber(
    saved.pdfFileRevision ?? saved.pdfFile?.output_revision,
    -1
  );
  state.pricingMatches = Array.isArray(saved.pricingMatches) ? saved.pricingMatches : [];
  state.pricingIssues = [];
  state.activeJob = normalizeActiveJob(saved.activeJob || {});
  renderFiles();
  renderPricingMatches(state.outputRows.length ? state.outputRows : state.pricingMatches, { fromPricingMatches: !state.outputRows.length && state.pricingMatches.length });
  renderMatchSummary({ pricing_matches: state.pricingMatches });
  clearPricingReviewMessages();
  if (state.aiFailed) {
    clearAiFailedDraftState();
    renderBasisFailureState();
  } else if (state.lineItems.length || state.quoteBasisSections.length || Object.values(state.quoteBasis).some((value) => splitLines(value).length > 0)) {
    updateQuoteBasisCard(saved.draftSource || "restored");
  } else {
    renderBasisEmptyState();
  }
  updateDownloadButton();
  setResultStatus(state.aiFailed ? "No usable AI draft" : state.downloadFile ? "Completed" : "No job yet", state.aiFailed ? "is-bad" : state.downloadFile ? "is-ok" : "");
  setWorkflowStage(restoredWorkflowStage(saved));
  if (state.aiFailed) {
    showAiFailureBanner();
  } else {
    clearAiFailureBanner();
  }
  const restoredPanel = restoredQuoteSessionSidePanel(saved, options);
  setSidePanel(restoredPanel, { force: true });
  return true;
}

async function restoreSessionState() {
  const saved = safeSessionJson();
  const currentScope = currentBrowserRecoveryScope();
  if (!saved) {
    await clearSessionFiles().catch(() => {});
    return false;
  }
  if (saved.version !== QUOTE_SESSION_STATE_VERSION || saved.browserRecoveryScope !== currentScope) {
    await purgeBrowserRecoveryState();
    return false;
  }
  return applyQuoteSessionSnapshot(saved);
}

function clearLegacyLocalCompanyPresets() {
  try {
    window.localStorage.removeItem(LEGACY_QUOTE_PRESETS_STORAGE_KEY);
  } catch {
    // Ignore storage failures; repo-backed presets still work.
  }
}

function selectedPresetId() {
  return state.selectedPresetValue || elements.presetSelect.value || "";
}

function templateProfilePresets() {
  return state.profiles.flatMap((profile) => {
    const presets = Array.isArray(profile.quote_detail_presets) ? profile.quote_detail_presets : [];
    return presets.map((preset) => ({
      ...preset,
      profile_id: preset.profile_id || profile.id,
      profile_label: profile.label || profile.id,
      source: "profile",
    }));
  });
}

function selectableTemplateProfilePresets() {
  return templateProfilePresets().filter((preset) => preset.id !== "default");
}

function normalizeCompanyProfile(profile = {}) {
  const label = safeProfileLabel(profile.label || profile.name || profile.id, "Company Profile");
  const id = safeProfileId(profile.id || label, `profile-${Date.now().toString(36)}`);
  const defaults = profile.defaults && typeof profile.defaults === "object" ? profile.defaults : {};
  return {
    ...profile,
    id,
    label,
    defaults,
  };
}

function companyProfilePresets() {
  return state.companyProfiles.map((profile) => {
    const normalized = normalizeCompanyProfile(profile);
    return {
      id: normalized.id,
      name: normalized.label,
      details: normalized.defaults || {},
      source: "company",
      saved_at: normalized.saved_at || "",
    };
  });
}

function profilePresets() {
  return [...templateProfilePresets(), ...companyProfilePresets()];
}

function defaultProfilePresetId() {
  const profile = currentProfile();
  const configured = profile.default_quote_detail_preset || "default";
  const presets = templateProfilePresets();
  if (presets.some((preset) => preset.id === "default")) return "default";
  if (configured && presets.some((preset) => preset.id === configured)) return configured;
  return presets[0]?.id || "";
}

function presetOptionValue(preset = {}) {
  const presetId = String(preset.id || "").trim();
  if (!presetId) return "";
  return preset.source === "company" ? companyProfileOptionValue(presetId) : profilePresetOptionValue(presetId);
}

function defaultPresetOptionValue() {
  const profileDefault = defaultProfilePresetId();
  if (profileDefault) return profilePresetOptionValue(profileDefault);
  return "";
}

function configuredProfilePresetId() {
  const profile = currentProfile();
  const configured = profile.default_quote_detail_preset || "";
  const presets = templateProfilePresets();
  return configured && presets.some((preset) => preset.id === configured) ? configured : "";
}

function selectedPreset() {
  const value = selectedPresetId();
  if (value.startsWith(PROFILE_PRESET_PREFIX)) {
    const presetId = value.slice(PROFILE_PRESET_PREFIX.length);
    const preset = templateProfilePresets().find((item) => item.id === presetId);
    return preset ? { ...preset, source: "profile" } : null;
  }
  if (value.startsWith(COMPANY_PROFILE_PRESET_PREFIX)) {
    const presetId = value.slice(COMPANY_PROFILE_PRESET_PREFIX.length);
    const preset = companyProfilePresets().find((item) => item.id === presetId);
    return preset ? { ...preset, source: "company" } : null;
  }
  return null;
}

function normalizePresetComparisonValue(value) {
  if (Array.isArray(value)) return value.map(normalizePresetComparisonValue).join("\n");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function quoteDetailsMatchPreset(savedDetails = {}, presetDetails = {}) {
  const savedCompany = savedDetails.company || {};
  const presetCompany = presetDetails.company || {};
  const savedRichText = savedDetails.rich_text || {};
  const presetRichText = presetDetails.rich_text || {};
  const companyNameMatches = presetCompany.name
    && normalizePresetComparisonValue(savedCompany.name) === normalizePresetComparisonValue(presetCompany.name);
  const headerDetailsMatch = presetCompany.header_details
    && normalizePresetComparisonValue(savedCompany.header_details) === normalizePresetComparisonValue(presetCompany.header_details);
  const richHeaderMatches = presetRichText.headerDetails
    && normalizePresetComparisonValue(savedRichText.headerDetails) === normalizePresetComparisonValue(presetRichText.headerDetails);
  return Boolean(companyNameMatches && (headerDetailsMatch || richHeaderMatches));
}

function presetValueFromQuoteDetails(savedDetails = {}) {
  const matchesDetails = (preset) => quoteDetailsMatchPreset(savedDetails, preset.details || {});
  const companyPreset = companyProfilePresets().find(matchesDetails);
  if (companyPreset) return companyProfileOptionValue(companyPreset.id);
  const profilePreset = templateProfilePresets().find(matchesDetails);
  if (profilePreset) return profilePresetOptionValue(profilePreset.id);
  return "";
}

function availablePresetValues() {
  return new Set([
    ...selectableTemplateProfilePresets().map((preset) => profilePresetOptionValue(preset.id)),
    ...companyProfilePresets().map((preset) => companyProfileOptionValue(preset.id)),
  ]);
}

function firstAvailablePresetValue() {
  return [...availablePresetValues()][0] || "";
}

function selectPresetValue(value = "") {
  const presetValue = String(value || "").trim();
  if (!presetValue || !availablePresetValues().has(presetValue)) return false;
  state.selectedPresetValue = presetValue;
  if (elements.presetSelect) elements.presetSelect.value = presetValue;
  return true;
}

function lastSelectedPresetValue() {
  const value = String(safeLastSelectionJson().presetValue || "").trim();
  return value && availablePresetValues().has(value) ? value : "";
}

function persistLastProfilePresetSelection(value = state.selectedPresetValue) {
  const presetValue = String(value || "").trim();
  if (!presetValue || !availablePresetValues().has(presetValue)) return;
  saveLastSelectionPatch({ presetValue });
}

function renderPresetStatus(message = "") {
  if (!elements.presetStatus) return;
  elements.presetStatus.textContent = message || "Currently active: Default";
}

function profileActionsMenuIsOpen() {
  return Boolean(elements.presetActionsMenu && !elements.presetActionsMenu.hidden);
}

function buttonCanAcceptClick(button) {
  return Boolean(button && !button.hidden && !button.disabled && button.getAttribute?.("aria-disabled") !== "true");
}

function focusActionButton(button) {
  if (buttonCanAcceptClick(button)) button.focus();
}

function queueActionButtonFocus(button) {
  focusActionButton(button);
  window.setTimeout(() => focusActionButton(button), 0);
}

function profileActionsMenuItems() {
  return [elements.exportPresetButton, elements.importPresetButton, elements.deletePresetButton]
    .filter(buttonCanAcceptClick);
}

function focusFirstProfileActionsMenuItem() {
  const [firstItem] = profileActionsMenuItems();
  if (firstItem) firstItem.focus();
}

function closeProfileActionsMenu(options = {}) {
  if (elements.presetActionsMenu) elements.presetActionsMenu.hidden = true;
  if (elements.profileActionsMenuButton) {
    elements.profileActionsMenuButton.setAttribute("aria-expanded", "false");
    if (options.focusButton) elements.profileActionsMenuButton.focus();
  }
}

function openProfileActionsMenu(options = {}) {
  if (!elements.profileActionsMenuButton || !elements.presetActionsMenu || elements.profileActionsMenuButton.disabled) return;
  elements.presetActionsMenu.hidden = false;
  elements.profileActionsMenuButton.setAttribute("aria-expanded", "true");
  if (options.focusFirst) queueActionButtonFocus(profileActionsMenuItems()[0]);
}

function toggleProfileActionsMenu(event) {
  event?.preventDefault();
  if (profileActionsMenuIsOpen()) {
    closeProfileActionsMenu();
  } else {
    openProfileActionsMenu({ focusFirst: event?.detail === 0 });
  }
}

function handleProfileActionsDocumentClick(event) {
  if (!profileActionsMenuIsOpen()) return;
  if (elements.profileActionsShell?.contains(event.target)) return;
  closeProfileActionsMenu();
}

function handleProfileActionsMenuKeydown(event) {
  const menuItems = profileActionsMenuItems();
  if (!profileActionsMenuIsOpen()) {
    if (event.target === elements.profileActionsMenuButton && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      openProfileActionsMenu({ focusFirst: true });
    }
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    closeProfileActionsMenu({ focusButton: true });
    return;
  }
  if (event.target === elements.profileActionsMenuButton && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    focusFirstProfileActionsMenuItem();
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  if (!menuItems.length) return;
  event.preventDefault();
  const activeIndex = menuItems.indexOf(document.activeElement);
  if (event.key === "Home") {
    menuItems[0].focus();
    return;
  }
  if (event.key === "End") {
    menuItems[menuItems.length - 1].focus();
    return;
  }
  const step = event.key === "ArrowUp" ? -1 : 1;
  const nextIndex = activeIndex < 0
    ? (step > 0 ? 0 : menuItems.length - 1)
    : (activeIndex + step + menuItems.length) % menuItems.length;
  menuItems[nextIndex].focus();
}

function profilePackPayloadForSave() {
  return state.pendingProfilePack && typeof state.pendingProfilePack === "object"
    ? state.pendingProfilePack
    : null;
}

function importedProfilePackPayload(data = {}) {
  const profile = data.profile && typeof data.profile === "object" ? data.profile : {};
  const pack = data.pack || data.profile_pack || profile.pack || profile.profile_pack;
  return pack && typeof pack === "object" ? pack : null;
}

function clearPendingProfilePack() {
  state.pendingProfilePack = null;
}

function renderHeaderLogoPreview() {
  if (!elements.headerLogoPreview) return;
  if (state.headerLogo) {
    elements.headerLogoPreview.classList.add("has-logo");
    elements.headerLogoPreview.innerHTML = `<img src="${escapeHtml(state.headerLogo.data_url)}" alt="Selected header logo preview">`;
    return;
  }
  elements.headerLogoPreview.classList.remove("has-logo");
  elements.headerLogoPreview.innerHTML = "<span>No logo loaded</span>";
}

function renderPresetOptions() {
  const builtInPresets = selectableTemplateProfilePresets();
  const savedPresets = companyProfilePresets();
  const availableValues = availablePresetValues();
  const selectedValue = [
    state.selectedPresetValue,
    elements.presetSelect.value,
    lastSelectedPresetValue(),
  ].find((value) => value && availableValues.has(value)) || "";
  const builtInOptions = builtInPresets
    .map((preset) => `<option value="${escapeHtml(profilePresetOptionValue(preset.id))}">${escapeHtml(preset.name)}</option>`)
    .join("");
  const savedOptions = savedPresets
    .map((preset) => `<option value="${escapeHtml(companyProfileOptionValue(preset.id))}">${escapeHtml(preset.name)}</option>`)
    .join("");
  const optionGroups = [
    builtInOptions ? `<optgroup label="Profile Templates">${builtInOptions}</optgroup>` : "",
    savedOptions ? `<optgroup label="Saved Profiles">${savedOptions}</optgroup>` : "",
  ].join("");
  elements.presetSelect.innerHTML = optionGroups || '<option value="">No saved profiles yet</option>';
  state.selectedPresetValue = selectedValue;
  elements.presetSelect.value = state.selectedPresetValue;
  elements.presetSelect.disabled = !availableValues.size;
  elements.presetSelect.title = availableValues.size ? "" : "Save or import a profile to load it here.";
  elements.presetSelect.setAttribute("aria-disabled", String(elements.presetSelect.disabled));
  updatePresetButtons();
}

function canManageProfiles() {
  return Boolean(state.permissions?.canManageProfiles);
}

function profileNoAccessReason() {
  return "You do not have access to save, import, or delete company profiles.";
}

function updatePresetSourceBadge(preset = selectedPreset()) {
  if (!elements.presetSourceBadge) return;
  elements.presetSourceBadge.textContent = preset?.source === "company" ? "Saved profile" : "Profile template";
}

function setButtonLabel(button, label) {
  if (!button) return;
  const labelElement = button.querySelector("span");
  if (labelElement) {
    labelElement.textContent = label;
  } else {
    button.textContent = label;
  }
}

function updatePresetButtons() {
  const preset = selectedPreset();
  const busy = Boolean(state.profileSaveBusy || state.profileDeleteBusy || appIsBusy());
  const canManage = canManageProfiles();
  updatePresetSourceBadge(preset);
  if (elements.loadPresetButton) {
    const hasPreset = Boolean(preset);
    const disabled = !hasPreset || busy;
    elements.loadPresetButton.disabled = disabled;
    setButtonLabel(elements.loadPresetButton, "Load");
    elements.loadPresetButton.title = busy
      ? "Profile operation is still running."
      : hasPreset
        ? "Load the selected quote company profile into the form."
        : "Choose a profile first.";
    elements.loadPresetButton.setAttribute("aria-disabled", String(disabled));
  }
  if (elements.deletePresetButton) {
    const hasPreset = Boolean(preset);
    const canOpenDeletePrompt = canManage && hasPreset;
    const canDelete = canOpenDeletePrompt && preset?.source === "company";
    const readOnlyPrompt = canOpenDeletePrompt && !canDelete && !busy;
    const reason = !canManage
      ? profileNoAccessReason()
      : !hasPreset
        ? "Choose a profile first."
        : preset?.source === "company"
          ? ""
          : "Profile templates are read-only.";
    elements.deletePresetButton.disabled = !canOpenDeletePrompt || busy;
    setButtonLabel(elements.deletePresetButton, state.profileDeleteBusy ? "Deleting..." : "Delete Profile");
    elements.deletePresetButton.title = busy ? "Profile operation is still running." : reason || "Delete this saved company profile.";
    elements.deletePresetButton.setAttribute("aria-disabled", String(elements.deletePresetButton.disabled));
    if (elements.deletePresetButton.dataset) {
      if (readOnlyPrompt) {
        elements.deletePresetButton.dataset.profileDeleteReadonly = "true";
      } else {
        delete elements.deletePresetButton.dataset.profileDeleteReadonly;
      }
    }
  }
  if (elements.savePresetButton) {
    const disabled = !canManage || busy;
    elements.savePresetButton.disabled = disabled;
    setButtonLabel(elements.savePresetButton, state.profileSaveBusy ? "Saving..." : "Save New");
    elements.savePresetButton.title = !canManage
      ? profileNoAccessReason()
      : "Save this quote company profile.";
    elements.savePresetButton.setAttribute("aria-disabled", String(disabled));
  }
  if (elements.importPresetButton) {
    elements.importPresetButton.disabled = !canManage || busy;
    setButtonLabel(elements.importPresetButton, "Import Profile");
    elements.importPresetButton.title = !canManage ? profileNoAccessReason() : "";
    elements.importPresetButton.setAttribute("aria-disabled", String(elements.importPresetButton.disabled));
  }
  if (elements.exportPresetButton) {
    elements.exportPresetButton.disabled = busy;
    setButtonLabel(elements.exportPresetButton, "Export Profile");
    elements.exportPresetButton.setAttribute("aria-disabled", String(elements.exportPresetButton.disabled));
  }
  if (elements.profileActionsMenuButton) {
    elements.profileActionsMenuButton.disabled = busy;
    elements.profileActionsMenuButton.title = busy ? "Profile operation is still running." : "Show export, import, and delete profile actions.";
    elements.profileActionsMenuButton.setAttribute("aria-disabled", String(elements.profileActionsMenuButton.disabled));
    if (busy) closeProfileActionsMenu();
  }
}

function handlePresetSelectChange() {
  const value = elements.presetSelect.value || "";
  state.selectedPresetValue = value;
  persistLastProfilePresetSelection(value);
  clearPendingProfilePack();
  const preset = selectedPreset();
  updatePresetSourceBadge(preset);
  updatePresetButtons();
  renderPresetStatus(preset ? `Selected "${preset.name}". Click Load to apply it.` : "Choose a preset to load.");
}

function profileNameFallbackLabel(profile = null) {
  const selected = selectedPreset();
  return safeProfileLabel(
    profile?.label || selected?.name || elements.quoteCompanyName?.value || "",
    "Company Profile"
  );
}

function hideProfileNameModal(options = {}) {
  if (state.profileSaveBusy && !options.force) return;
  hideProfileOverwriteModal({ force: true });
  state.profileNameMode = "";
  state.profileNamePendingProfile = null;
  state.profileNameError = "";
  if (elements.profileNameModal) {
    elements.profileNameModal.classList.remove("is-open");
    elements.profileNameModal.hidden = true;
  }
  if (elements.profileNameError) {
    elements.profileNameError.hidden = true;
    elements.profileNameError.textContent = "";
  }
}

function renderProfileNameModal() {
  const modal = elements.profileNameModal;
  if (!modal) return;
  const mode = state.profileNameMode || "";
  if (!mode) {
    modal.classList.remove("is-open");
    modal.hidden = true;
    return;
  }
  const isImport = mode === "import";
  modal.hidden = false;
  modal.classList.add("is-open");
  if (elements.profileNameEyebrow) elements.profileNameEyebrow.textContent = isImport ? "Imported Profile" : "Saved Profile";
  if (elements.profileNameTitle) elements.profileNameTitle.textContent = isImport ? "Save imported profile" : "Save company profile";
  if (elements.profileNameText) {
    elements.profileNameText.textContent = isImport
      ? "Confirm the reusable name. Saving this imported profile will overwrite the current profile settings defaults."
      : "Name this reusable quote company profile.";
  }
  if (elements.profileNameError) {
    const message = String(state.profileNameError || "").trim();
    elements.profileNameError.hidden = !message;
    elements.profileNameError.textContent = message;
  }
  [elements.profileNameInput, elements.cancelProfileNameButton, elements.confirmProfileNameButton].forEach((control) => {
    if (!control) return;
    control.disabled = state.profileSaveBusy;
    control.setAttribute("aria-disabled", String(control.disabled));
  });
  if (elements.confirmProfileNameButton) {
    elements.confirmProfileNameButton.textContent = state.profileSaveBusy ? "Saving..." : "Save";
  }
}

function openProfileNameModal(options = {}) {
  if (!canManageProfiles() || state.profileSaveBusy || appIsBusy()) {
    renderPresetStatus(profileNoAccessReason());
    updatePresetButtons();
    return;
  }
  state.profileNameMode = options.mode || "save";
  state.profileNamePendingProfile = options.profile || null;
  state.profileNameError = "";
  if (elements.profileNameInput) {
    elements.profileNameInput.value = state.profileNameMode === "import" ? profileNameFallbackLabel(options.profile) : "";
  }
  renderProfileNameModal();
  window.setTimeout(() => {
    elements.profileNameInput?.focus();
    elements.profileNameInput?.select();
  }, 0);
}

function profilePayloadForLabel(label = "", options = {}) {
  const sourceProfile = options.profile || null;
  const existingProfile = options.existingProfile || null;
  const profileId = safeProfileId(existingProfile?.id || label, sourceProfile?.id || `profile-${Date.now().toString(36)}`);
  const payload = {
    id: profileId,
    label,
    description: sourceProfile?.description || existingProfile?.description || "Saved from the Quote Company panel.",
    defaults: sourceProfile?.defaults || collectQuoteCompanyProfileDetails(),
  };
  const pendingPack = profilePackPayloadForSave();
  if (pendingPack) {
    payload.pack = pendingPack;
  }
  return payload;
}

function existingCompanyProfileForLabel(label = "", options = {}) {
  const safeLabel = safeProfileLabel(label, "");
  const candidateId = safeProfileId(safeLabel, options.profile?.id || "");
  const candidateLabel = safeLabel.toLowerCase();
  return state.companyProfiles.find((profile) => {
    const profileId = safeProfileId(profile?.id || profile?.label, "");
    const profileLabel = safeProfileLabel(profile?.label || profile?.name || profile?.id, "").toLowerCase();
    return Boolean((candidateId && profileId === candidateId) || (candidateLabel && profileLabel === candidateLabel));
  }) || null;
}

function hideProfileOverwriteModal(options = {}) {
  if (state.profileSaveBusy && !options.force) return;
  state.profileOverwriteConfirmLabel = "";
  state.profileOverwriteConfirmOptions = null;
  if (elements.profileOverwriteModal) {
    elements.profileOverwriteModal.classList.remove("is-open");
    elements.profileOverwriteModal.hidden = true;
  }
  if (options.focusInput) elements.profileNameInput?.focus();
}

function renderProfileOverwriteModal() {
  const modal = elements.profileOverwriteModal;
  if (!modal) return;
  const label = safeProfileLabel(state.profileOverwriteConfirmLabel, "");
  if (!label) {
    modal.classList.remove("is-open");
    modal.hidden = true;
    return;
  }
  const isImport = state.profileOverwriteConfirmOptions?.mode === "import";
  modal.hidden = false;
  modal.classList.add("is-open");
  if (elements.profileOverwriteTitle) elements.profileOverwriteTitle.textContent = `Overwrite "${label}"?`;
  if (elements.profileOverwriteText) {
    const titleText = `A profile named "${label}" already exists.`;
    const detailText = isImport
      ? "Overwrite it with the imported quote company defaults? The current profile settings defaults will be replaced after saving."
      : "Overwrite it with the current quote company defaults? The saved profile settings will be replaced.";
    if (typeof elements.profileOverwriteText.querySelector === "function") {
      const title = elements.profileOverwriteText.querySelector("strong");
      const detail = elements.profileOverwriteText.querySelector("span");
      if (title) title.textContent = titleText;
      if (detail) detail.textContent = detailText;
    } else {
      elements.profileOverwriteText.textContent = `${titleText} ${detailText}`;
    }
  }
  [elements.cancelProfileOverwriteButton, elements.confirmProfileOverwriteButton].forEach((button) => {
    if (!button) return;
    button.disabled = state.profileSaveBusy || state.profileDeleteBusy || appIsBusy();
    button.setAttribute("aria-disabled", String(button.disabled));
  });
}

function requestProfileOverwriteConfirmation(label = "", options = {}) {
  state.profileOverwriteConfirmLabel = safeProfileLabel(label, "");
  state.profileOverwriteConfirmOptions = { ...options };
  renderProfileOverwriteModal();
  queueActionButtonFocus(elements.confirmProfileOverwriteButton);
}

async function confirmProfileOverwriteSave(event) {
  event?.preventDefault();
  const label = state.profileOverwriteConfirmLabel;
  const options = state.profileOverwriteConfirmOptions || {};
  if (!label) {
    hideProfileOverwriteModal({ force: true, focusInput: true });
    return;
  }
  hideProfileOverwriteModal({ force: true });
  await saveNamedCompanyProfile(label, { ...options, overwriteConfirmed: true });
}

function applySavedCompanyProfileProfile(profile = {}) {
  const defaults = profile.defaults && typeof profile.defaults === "object" ? profile.defaults : {};
  if (!Object.keys(defaults).length) return;
  applyQuoteDetails(defaults, { includeLogo: true, clearLogo: true, partial: true });
  clearGeneratedQuoteState();
  setWorkflowStage(state.images.length ? "ready_to_analyze" : "needs_images");
}

async function saveNamedCompanyProfile(label = "", options = {}) {
  const safeLabel = safeProfileLabel(label, "");
  if (!safeLabel) {
    state.profileNameError = "Enter a reusable profile name before saving.";
    renderPresetStatus("Enter a profile name before saving.");
    renderProfileNameModal();
    elements.profileNameInput?.focus();
    return;
  }
  const existingProfile = existingCompanyProfileForLabel(safeLabel, options);
  if (existingProfile && !options.overwriteConfirmed) {
    requestProfileOverwriteConfirmation(safeLabel, { ...options, existingProfile });
    return;
  }
  const payload = profilePayloadForLabel(safeLabel, { ...options, existingProfile });
  const isImport = options.mode === "import";
  const layoutCopy = profilePackPayloadForSave()?.quotation_layout ? " Layout workbook included." : "";
  state.profileSaveBusy = true;
  state.profileNameError = "";
  renderPresetStatus(`${isImport ? "Importing and saving" : "Saving"} "${safeLabel}"...${isImport ? layoutCopy : ""}`);
  renderProfileNameModal();
  updatePresetButtons();
  try {
    const { ok, data } = await postJson("/api/settings/profiles", payload);
    if (!ok) {
      const message = genericFailureMessages(data).join(" ");
      state.profileNameError = message;
      renderPresetStatus(message);
      renderProfileNameModal();
      return;
    }
    const saved = normalizeCompanyProfile(data.profile || payload);
    state.companyProfiles = [
      ...state.companyProfiles.filter((profile) => safeProfileId(profile.id || profile.label, "") !== saved.id),
      saved,
    ].sort((left, right) => String(left.label || left.id || "").localeCompare(String(right.label || right.id || ""), undefined, { sensitivity: "base" }));
    state.selectedPresetValue = companyProfileOptionValue(saved.id);
    persistLastProfilePresetSelection(state.selectedPresetValue);
    if (isImport) applySavedCompanyProfileProfile(saved);
    clearPendingProfilePack();
    hideProfileNameModal({ force: true });
    renderPresetOptions();
    renderPresetStatus(`${isImport ? "Imported and saved" : "Saved"} "${saved.label || safeLabel}".${isImport ? layoutCopy : ""}`);
  } catch (error) {
    const message = genericFailureMessages(error).join(" ");
    state.profileNameError = message;
    renderPresetStatus(message);
    renderProfileNameModal();
  } finally {
    state.profileSaveBusy = false;
    renderProfileNameModal();
    updatePresetButtons();
    syncControlStates();
  }
}

async function confirmProfileNameSave() {
  if (!canManageProfiles() || state.profileSaveBusy || appIsBusy()) {
    renderPresetStatus(profileNoAccessReason());
    updatePresetButtons();
    return;
  }
  const label = safeProfileLabel(elements.profileNameInput?.value, "");
  await saveNamedCompanyProfile(label, {
    mode: state.profileNameMode || "save",
    profile: state.profileNamePendingProfile,
  });
}

function saveCurrentPreset(event) {
  event?.preventDefault();
  openProfileNameModal({ mode: "save" });
}

function selectedPresetNameForLoad() {
  const preset = selectedPreset();
  return safeProfileLabel(preset?.name || preset?.id || "", "selected profile");
}

function hideProfileLoadModal(options = {}) {
  state.profileLoadConfirmValue = "";
  if (elements.profileLoadModal) {
    elements.profileLoadModal.classList.remove("is-open");
    elements.profileLoadModal.hidden = true;
  }
  if (options.focusButton) elements.loadPresetButton?.focus();
}

function renderProfileLoadModal() {
  const modal = elements.profileLoadModal;
  if (!modal) return;
  const preset = selectedPreset();
  if (!state.profileLoadConfirmValue || !preset) {
    modal.classList.remove("is-open");
    modal.hidden = true;
    return;
  }
  const label = selectedPresetNameForLoad();
  modal.hidden = false;
  modal.classList.add("is-open");
  if (elements.profileLoadTitle) elements.profileLoadTitle.textContent = `Load "${label}"?`;
  if (elements.profileLoadText) {
    const titleText = "This will replace the current quote company fields.";
    const detailText = `Unsaved edits in this section will be overwritten by "${label}" defaults.`;
    if (typeof elements.profileLoadText.querySelector === "function") {
      const title = elements.profileLoadText.querySelector("strong");
      const detail = elements.profileLoadText.querySelector("span");
      if (title) title.textContent = titleText;
      if (detail) detail.textContent = detailText;
    } else {
      elements.profileLoadText.textContent = `${titleText} ${detailText}`;
    }
  }
  [elements.cancelProfileLoadButton, elements.confirmProfileLoadButton].forEach((button) => {
    if (!button) return;
    button.disabled = state.profileSaveBusy || state.profileDeleteBusy || appIsBusy();
    button.setAttribute("aria-disabled", String(button.disabled));
  });
}

function requestSelectedPresetLoad(event) {
  event?.preventDefault();
  const preset = selectedPreset();
  if (!preset) {
    renderPresetStatus("Choose a preset to load.");
    updatePresetButtons();
    return;
  }
  state.profileLoadConfirmValue = elements.presetSelect.value || presetOptionValue(preset);
  renderProfileLoadModal();
  queueActionButtonFocus(elements.confirmProfileLoadButton);
}

function confirmSelectedPresetLoad(event) {
  event?.preventDefault();
  if (!state.profileLoadConfirmValue) {
    hideProfileLoadModal({ force: true });
    return;
  }
  loadSelectedPreset();
  hideProfileLoadModal({ force: true });
}

function loadCurrentPreset(event) {
  requestSelectedPresetLoad(event);
}

function loadSelectedPreset(options = {}) {
  const preset = selectedPreset();
  if (!preset) {
    renderPresetStatus("Choose a preset to load.");
    return;
  }
  state.selectedPresetValue = elements.presetSelect.value || presetOptionValue(preset);
  persistLastProfilePresetSelection(state.selectedPresetValue);
  clearPendingProfilePack();
  const shouldPreserveExistingQuoteState = options.silent === true && (quoteDraftHasAiAnalysis() || quoteDraftHasOutputState());
  const details = preset.details || {};
  const emptyDefaultProfilePreset = preset.source === "profile"
    && preset.id === "default"
    && details
    && typeof details === "object"
    && !Object.keys(details).length;
  if (!shouldPreserveExistingQuoteState) {
    const clearsLogo = Boolean(details.company && typeof details.company === "object");
    applyQuoteDetails(details, { includeLogo: true, clearLogo: clearsLogo, partial: true });
    if (emptyDefaultProfilePreset) {
      setInputValue(elements.headerDetails, "");
      setInputValue(elements.paymentTerms, "");
      setInputValue(elements.standardNotes, "");
      setInputValue(elements.quoteCompanyName, "");
      setInputValue(elements.companySignatory, "");
      setInputValue(elements.companyTitle, "");
      state.headerLogo = null;
      if (elements.headerLogoInput) elements.headerLogoInput.value = "";
      applyDefaultQuoteCompanyFields();
      renderHeaderLogoPreview();
    }
  }
  const shouldResetGeneratedState = options.resetGeneratedState !== false && !shouldPreserveExistingQuoteState && options.silent !== true;
  if (shouldResetGeneratedState) {
    clearGeneratedQuoteState();
    setWorkflowStage(state.images.length ? "ready_to_analyze" : "needs_images");
  }
  syncControlStates();
  renderPresetStatus(`Loaded "${preset.name}".`);
}

function loadDefaultProfilePreset(options = {}) {
  const defaultPreset = options.preferLastSelection === false
    ? defaultPresetOptionValue()
    : lastSelectedPresetValue() || defaultPresetOptionValue();
  if (!defaultPreset) return;
  state.selectedPresetValue = defaultPreset;
  elements.presetSelect.value = availablePresetValues().has(state.selectedPresetValue) ? state.selectedPresetValue : "";
  loadSelectedPreset(options);
  if (!availablePresetValues().has(defaultPreset)) {
    state.selectedPresetValue = "";
    elements.presetSelect.value = "";
    updatePresetButtons();
  }
}

function loadConfiguredProfilePreset(options = {}) {
  const configuredPreset = configuredProfilePresetId();
  if (!configuredPreset) {
    loadDefaultProfilePreset(options);
    return;
  }
  state.selectedPresetValue = profilePresetOptionValue(configuredPreset);
  elements.presetSelect.value = availablePresetValues().has(state.selectedPresetValue) ? state.selectedPresetValue : "";
  loadSelectedPreset(options);
  if (!availablePresetValues().has(state.selectedPresetValue)) {
    state.selectedPresetValue = "";
    elements.presetSelect.value = "";
    updatePresetButtons();
  }
}

function clearCustomerDetails() {
  setInputValue(elements.clientName, "");
  setInputValue(elements.clientAttention, "");
  setInputValue(elements.clientTitle, "");
  setInputValue(elements.clientAddress, "");
  setInputValue(elements.projectTitle, "");
  setInputValue(elements.showName, "");
  state.boothDimensions = { ...DEFAULT_BOOTH_DIMENSIONS };
  setInputValue(elements.quoteDate, todayDateInputValue());
  applyQuoteDateFormatFromHtml("");
  setInputValue(elements.projectNumber, "");
  resetQuoteCommercialFieldsToSelectedPricingReference();
  clearGeneratedQuoteState();
  renderProfileOptions();
  setWorkflowStage(state.images.length ? "ready_to_analyze" : "needs_images");
  syncControlStates();
}

function clearQuoteCompanyDetails() {
  setInputValue(elements.headerDetails, "");
  setInputValue(elements.termsHeading, "");
  setInputValue(elements.paymentTerms, "");
  setInputValue(elements.notesHeading, "");
  setInputValue(elements.standardNotes, "");
  setInputValue(elements.quoteCompanyName, "");
  setInputValue(elements.acceptanceText, "");
  setInputValue(elements.companySignatory, "");
  setInputValue(elements.companyTitle, "");
  setInputValue(elements.companyDateLabel, "");
  setInputValue(elements.personLabel, "");
  setInputValue(elements.stampLabel, "");
  setInputValue(elements.dateLabel, "");
  applyDefaultQuoteCompanyFields();
  state.headerLogo = null;
  elements.headerLogoInput.value = "";
  state.selectedPresetValue = "";
  clearPendingProfilePack();
  hideProfileNameModal({ force: true });
  clearGeneratedQuoteState();
  setWorkflowStage(state.images.length ? "ready_to_analyze" : "needs_images");
  updatePresetButtons();
  renderHeaderLogoPreview();
  loadDefaultProfilePreset({ silent: true, preferLastSelection: false });
  syncControlStates();
  renderPresetStatus("Quote-company defaults reset to the Default profile template.");
}

function resetImagesDraft() {
  if (appIsBusy()) return;
  state.images = [];
  if (elements.imageInput) elements.imageInput.value = "";
  setImageUploadStatus("");
  clearGeneratedQuoteState();
  renderFiles();
  setWorkflowStage("needs_images");
  syncControlStates();
}

async function startNewQuote() {
  if (appIsBusy()) return;
  clearSessionState();
  resetCurrentQuoteDraftState();
  showQuoteFlow();
  syncControlStates();
}

function resetCurrentQuoteDraftState() {
  clearQuoteSessionDraftSaveTimer();
  state.quoteSessionId = "";
  state.quoteSessionDraftSaveStarted = false;
  clearRestoredQuoteSessionBaseline();
  resetQuoteCommercialTouched();
  state.profileId = "";
  state.pricingReferenceId = "";
  state.pricingReferenceSource = "";
  state.images = [];
  setImageUploadStatus("");
  state.headerLogo = null;
  state.boothDimensions = { ...DEFAULT_BOOTH_DIMENSIONS };
  state.pendingFeedback = "";
  state.downloadFile = null;
  state.pdfFile = null;
  elements.imageInput.value = "";
  elements.headerLogoInput.value = "";
  state.selectedPresetValue = "";
  elements.presetSelect.value = "";
  clearPendingProfilePack();
  hideProfileNameModal({ force: true });
  applyQuoteDetails({}, { clearLogo: true });
  applyDefaultQuoteCompanyFields();
  clearGeneratedQuoteState();
  renderFiles();
  renderProfileOptions();
  renderPresetOptions();
  loadDefaultProfilePreset({ silent: true });
  renderHeaderLogoPreview();
  renderPresetStatus("Started a new quote.");
  setWorkflowStage("needs_images");
  setSidePanel("images", { force: true });
}

function profileDeleteConfirmPreset() {
  const presetId = String(state.profileDeleteConfirmId || "").trim();
  if (!presetId) return null;
  return companyProfilePresets().find((preset) => preset.id === presetId) || null;
}

function hideProfileDeleteModal(options = {}) {
  if (state.profileDeleteBusy && !options.force) return;
  state.profileDeleteConfirmId = "";
  state.profileDeleteReadOnlyName = "";
  state.profileDeleteError = "";
  if (elements.profileDeleteModal) {
    elements.profileDeleteModal.classList.remove("is-open");
    elements.profileDeleteModal.hidden = true;
  }
  if (elements.profileDeleteError) {
    elements.profileDeleteError.hidden = true;
    elements.profileDeleteError.textContent = "";
  }
}

function renderProfileDeleteModal() {
  const modal = elements.profileDeleteModal;
  if (!modal) return;
  const preset = profileDeleteConfirmPreset();
  const readOnlyLabel = safeProfileLabel(state.profileDeleteReadOnlyName, "");
  if (!preset && !readOnlyLabel) {
    modal.classList.remove("is-open");
    modal.hidden = true;
    return;
  }
  const label = preset?.name || preset?.id || readOnlyLabel || "this profile";
  const isReadOnlyNotice = !preset && Boolean(readOnlyLabel);
  modal.hidden = false;
  modal.classList.add("is-open");
  if (elements.profileDeleteTitle) {
    elements.profileDeleteTitle.textContent = isReadOnlyNotice ? `Cannot delete "${label}"` : `Delete "${label}"?`;
  }
  if (elements.profileDeleteText) {
    const titleText = isReadOnlyNotice ? "Profile templates are read-only." : "This removes the saved company profile.";
    const detailText = isReadOnlyNotice
      ? "Select a saved profile if you need to delete one."
      : "Quote details already filled from it are not changed.";
    if (typeof elements.profileDeleteText.querySelector === "function") {
      const title = elements.profileDeleteText.querySelector("strong");
      const detail = elements.profileDeleteText.querySelector("span");
      if (title) title.textContent = titleText;
      if (detail) detail.textContent = detailText;
    } else {
      elements.profileDeleteText.textContent = `${titleText} ${detailText}`;
    }
  }
  if (elements.profileDeleteError) {
    const message = String(state.profileDeleteError || "").trim();
    elements.profileDeleteError.hidden = !message;
    elements.profileDeleteError.textContent = message;
  }
  [elements.cancelProfileDeleteButton, elements.confirmProfileDeleteButton].forEach((button) => {
    if (!button) return;
    button.disabled = state.profileDeleteBusy;
    button.setAttribute("aria-disabled", String(button.disabled));
  });
  if (elements.cancelProfileDeleteButton) {
    elements.cancelProfileDeleteButton.textContent = isReadOnlyNotice ? "Close" : "Cancel";
  }
  if (elements.confirmProfileDeleteButton) {
    elements.confirmProfileDeleteButton.hidden = isReadOnlyNotice;
    elements.confirmProfileDeleteButton.textContent = state.profileDeleteBusy ? "Deleting..." : "Delete";
  }
}

function requestSelectedPresetDelete() {
  const preset = selectedPreset();
  if (!canManageProfiles()) {
    renderPresetStatus(profileNoAccessReason());
    updatePresetButtons();
    return;
  }
  if (!preset || preset.source !== "company") {
    state.profileDeleteConfirmId = "";
    state.profileDeleteReadOnlyName = preset?.name || preset?.id || "this profile template";
    state.profileDeleteError = "";
    renderProfileDeleteModal();
    queueActionButtonFocus(elements.cancelProfileDeleteButton);
    updatePresetButtons();
    return;
  }
  state.profileDeleteConfirmId = preset.id || "";
  state.profileDeleteReadOnlyName = "";
  state.profileDeleteError = "";
  renderProfileDeleteModal();
  queueActionButtonFocus(elements.confirmProfileDeleteButton);
}

async function deleteSelectedPreset() {
  const preset = profileDeleteConfirmPreset();
  if (!preset || preset.source !== "company") {
    hideProfileDeleteModal({ force: true });
    updatePresetButtons();
    return;
  }
  const label = preset.name || preset.id || "this saved profile";
  state.profileDeleteBusy = true;
  state.profileDeleteError = "";
  renderPresetStatus(`Deleting "${label}"...`);
  updatePresetButtons();
  renderProfileDeleteModal();
  try {
    const response = await fetch(`/api/settings/profiles/${encodeURIComponent(preset.id)}`, {
      method: "DELETE",
      headers: state.csrfToken ? { [state.csrfHeaderName]: state.csrfToken } : {},
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = genericFailureMessages(data).join(" ");
      state.profileDeleteError = message;
      renderPresetStatus(message);
      return;
    }
    state.companyProfiles = state.companyProfiles.filter((profile) => safeProfileId(profile.id || profile.label, "") !== preset.id);
    state.selectedPresetValue = lastSelectedPresetValue() || "";
    renderPresetOptions();
    renderPresetStatus(response.status === 404 ? `"${label}" was not found.` : `Deleted "${label}".`);
    hideProfileDeleteModal();
  } catch (error) {
    const message = genericFailureMessages(error).join(" ");
    state.profileDeleteError = message;
    renderPresetStatus(message);
  } finally {
    state.profileDeleteBusy = false;
    renderProfileDeleteModal();
    updatePresetButtons();
    syncControlStates();
  }
}

function exportedCompanyProfilePayload(label = "") {
  const resolvedLabel = safeProfileLabel(label || elements.quoteCompanyName?.value, "Company Profile");
  const payload = {
    schema: COMPANY_PROFILE_EXPORT_SCHEMA,
    exported_at: new Date().toISOString(),
    workspace: state.workspace && typeof state.workspace === "object" ? {
      company_id: state.workspace.company?.id || "",
      company_slug: state.workspace.company?.slug || "",
      company_display_name: state.workspace.company?.display_name || "",
      workspace_id: state.workspace.workspace?.id || "",
      workspace_slug: state.workspace.workspace?.slug || "",
    } : undefined,
    profile: {
      id: safeProfileId(resolvedLabel, `profile-${Date.now().toString(36)}`),
      label: resolvedLabel,
      description: "Exported from the Swooshz Quote Company panel.",
      defaults: collectQuoteCompanyProfileDetails(),
    },
  };
  const pendingPack = profilePackPayloadForSave();
  if (pendingPack) {
    payload.pack = pendingPack;
  }
  return payload;
}

function downloadJsonFile(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  downloadBlobFile(filename, blob);
}

function downloadBlobFile(filename, blob) {
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

function filenameFromContentDisposition(value = "", fallback = "download.json") {
  const match = String(value || "").match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i);
  if (!match) return fallback;
  try {
    return decodeURIComponent(match[1]).trim() || fallback;
  } catch {
    return match[1].trim() || fallback;
  }
}

async function fetchCompanyProfileExport(profileId = "") {
  const safeId = safeProfileId(profileId, "");
  if (!safeId) {
    return { ok: false, data: { errors: ["Select a saved profile to export."] } };
  }
  const url = `/api/settings/profiles/${encodeURIComponent(safeId)}/export.json`;
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    const errorReference = newClientErrorReference();
    if (!state.isPageUnloading) {
      logClientEvent("client_error", fetchFailureLogDetails(url, { error_reference: errorReference }));
    }
    return { ok: false, data: { status: "failed", fetch_failed: true, error_reference: errorReference } };
  }
  if (!response.ok) {
    const data = await jsonFromResponse(response);
    logClientEvent("server_error", {
      url,
      status: response.status,
      error_reference: errorReferenceFrom(data),
      errors: data.errors || [],
    });
    return { ok: false, data, status: response.status };
  }
  const blob = await response.blob();
  return {
    ok: true,
    blob,
    filename: filenameFromContentDisposition(response.headers.get("Content-Disposition"), `${safeId}.quote-company-profile.json`),
  };
}

async function exportCurrentPreset(event) {
  event?.preventDefault();
  if (state.profileSaveBusy || state.profileDeleteBusy || appIsBusy()) return;
  const selected = selectedPreset();
  const label = selected?.name || elements.quoteCompanyName?.value || "Company Profile";
  let payload = null;
  if (selected?.source === "company" && !profilePackPayloadForSave()) {
    renderPresetStatus(`Exporting "${selected.name || selected.id}"...`);
    const { ok, data, blob, filename } = await fetchCompanyProfileExport(selected.id);
    if (!ok) {
      renderPresetStatus(genericFailureMessages(data).join(" "));
      return;
    }
    downloadBlobFile(filename, blob);
    renderPresetStatus(`Exported "${selected.name || selected.id}".`);
    return;
  } else {
    payload = exportedCompanyProfilePayload(label);
  }
  const filename = `${safeProfileId(payload.profile.label, "company-profile")}.quote-company-profile.json`;
  downloadJsonFile(filename, payload);
  renderPresetStatus(`Exported "${payload.profile.label}".`);
}

function normalizeImportedCompanyProfile(data = {}, fallbackLabel = "Imported Company Profile") {
  const profile = data.profile && typeof data.profile === "object" ? data.profile : data;
  const directDetails = ["company", "quote_text", "signature", "rich_text"].some((key) => (
    profile[key] && typeof profile[key] === "object"
  ));
  const defaults = profile.defaults && typeof profile.defaults === "object"
    ? profile.defaults
    : profile.details && typeof profile.details === "object"
      ? profile.details
      : data.defaults && typeof data.defaults === "object"
        ? data.defaults
        : data.details && typeof data.details === "object"
          ? data.details
          : directDetails
            ? profile
            : {};
  if (!Object.keys(defaults).length) {
    throw new Error("The selected file does not contain importable company profile defaults.");
  }
  const label = safeProfileLabel(profile.label || profile.name || data.label || fallbackLabel, fallbackLabel);
  return normalizeCompanyProfile({
    id: safeProfileId(profile.id || label, "imported-company-profile"),
    label,
    description: profile.description || data.description || "Imported company profile.",
    defaults,
  });
}

async function handlePresetImportFileChange() {
  const file = elements.importPresetFile?.files?.[0];
  if (!file) return;
  clearPendingProfilePack();
  try {
    const text = await fileToText(file);
    const data = JSON.parse(text);
    const imported = normalizeImportedCompanyProfile(data, file.name.replace(/\.json$/i, "") || "Imported Company Profile");
    const importedPack = importedProfilePackPayload(data);
    state.pendingProfilePack = importedPack;
    const layoutCopy = importedPack?.quotation_layout ? " Layout workbook included." : "";
    renderPresetStatus(`Ready to import "${imported.label}". Save it as a profile to overwrite the current profile settings defaults.${layoutCopy}`);
    openProfileNameModal({ mode: "import", profile: imported });
  } catch (error) {
    renderPresetStatus(error?.message || "Could not import that company profile JSON.");
  } finally {
    if (elements.importPresetFile) elements.importPresetFile.value = "";
    updatePresetButtons();
    syncControlStates();
  }
}

function requestPresetImport(event) {
  event?.preventDefault();
  if (!canManageProfiles() || state.profileSaveBusy || state.profileDeleteBusy || appIsBusy()) {
    renderPresetStatus(profileNoAccessReason());
    updatePresetButtons();
    return;
  }
  elements.importPresetFile?.click();
}

function renderProfileOptions() {
  if (!elements.profileSelect) return;
  const references = sortedPricingReferencesForDisplay(state.pricingReferences);
  const selectedValue = currentPricingReference() ? pricingReferenceSelectValue(currentPricingReference()) : "";
  const referenceOption = (reference) => {
    const referenceId = String(reference.id || "").trim();
    return `<option value="${escapeHtml(pricingReferenceSelectValue(reference))}">${escapeHtml(reference.label || referenceId)}</option>`;
  };
  elements.profileSelect.innerHTML = references.length
    ? references.map(referenceOption).join("")
    : `<option value="">${escapeHtml(MISSING_PRICING_REFERENCES_MESSAGE)}</option>`;
  const preferredReference = currentPricingReference() || lastSelectedPricingReference() || defaultPricingReference() || references[0] || null;
  if (preferredReference) {
    state.pricingReferenceId = preferredReference.id || "";
    state.pricingReferenceSource = pricingReferenceSelectionFromValue(pricingReferenceSelectValue(preferredReference)).source;
  } else {
    state.pricingReferenceId = "";
    state.pricingReferenceSource = "";
  }
  const selectedReference = currentPricingReference();
  elements.profileSelect.value = selectedReference ? pricingReferenceSelectValue(selectedReference) : selectedValue;
  elements.profileSelect.disabled = references.length === 0;
  elements.profileSelect.title = references.length ? "" : MISSING_PRICING_REFERENCES_MESSAGE;
  elements.profileSelect.setAttribute("aria-disabled", String(elements.profileSelect.disabled));
  renderSelectedPricingReferenceSummary();
  renderPricingReferenceDeleteOptions();
}

function firstPricingReferenceOptionValue() {
  const firstReference = sortedPricingReferencesForDisplay(state.pricingReferences)[0] || null;
  return firstReference ? pricingReferenceSelectValue(firstReference) : "";
}

function selectPricingReferenceOptionValue(value = "") {
  const selection = pricingReferenceSelectionFromValue(value || "");
  if (!selection.pricingReferenceId) return false;
  state.pricingReferenceId = selection.pricingReferenceId;
  state.pricingReferenceSource = selection.source;
  syncSelectedPricingReference();
  if (elements.profileSelect) elements.profileSelect.value = pricingReferenceSelectValue(currentPricingReference() || {});
  renderSelectedPricingReferenceSummary();
  renderPricingReferenceDeleteOptions();
  return true;
}

function canManagePricingReferences() {
  return Boolean(state.permissions?.canManagePricingReferences);
}

function pricingReferenceNoAccessReason() {
  return "You do not have access to manage pricing references.";
}

function protectedPricingReferenceReason(reference = currentPricingReference()) {
  if (!reference) return "Select a pricing reference first.";
  if (!canManagePricingReferences()) return pricingReferenceNoAccessReason();
  if (String(reference.source || "bundled") === "bundled") return "Bundled pricing reference packs are read-only.";
  if (String(reference.source || "bundled") !== "local") return "Only local pricing reference packs can be deleted here.";
  const referenceId = String(reference.id || "").trim();
  if (!referenceId) return "Select a pricing reference first.";
  const profileDefault = String(currentProfile()?.default_pricing_reference || state.defaultPricingReferenceId || DEFAULT_PRICING_REFERENCE_ID).trim();
  if (referenceId === DEFAULT_PRICING_REFERENCE_ID || referenceId === profileDefault) return "Default pricing references cannot be deleted.";
  return "";
}

function deletionPricingReference() {
  const select = elements.deletePricingReferenceSelect;
  if (!select) return currentPricingReference();
  const selection = pricingReferenceSelectionFromValue(select.value || "");
  return state.pricingReferences.find((reference) => (
    reference.id === selection.pricingReferenceId
    && pricingReferenceSelectValue(reference).startsWith(`${selection.source || "bundled"}::`)
  )) || null;
}

function canDeleteSelectedPricingReference() {
  return !protectedPricingReferenceReason(deletionPricingReference());
}

function pricingReferenceExportBlockReason(reference = deletionPricingReference()) {
  if (!reference) return "Select a pricing reference first.";
  if (!canManagePricingReferences()) return pricingReferenceNoAccessReason();
  if (String(reference.source || "bundled") !== "local") return "Only local pricing reference packs can be exported here.";
  if (!String(reference.id || "").trim()) return "Select a pricing reference first.";
  return "";
}

function updatePricingReferenceExportButton() {
  const button = elements.exportPricingReferenceButton;
  if (!button) return;
  const mode = normalizePricingReferenceSettingsMode(state.pricingReferenceSettingsMode);
  const shouldShow = mode === PRICING_REFERENCE_SETTINGS_MODE_MANAGE && canManagePricingReferences();
  button.hidden = !shouldShow;
  const reason = pricingReferenceExportBlockReason(deletionPricingReference());
  const busy = pricingReferenceOperationBusy();
  button.disabled = !shouldShow || Boolean(reason) || busy;
  button.title = busy ? "Pricing reference operation is still running." : reason || "Export this pricing reference as an importable Excel workbook.";
  button.setAttribute("aria-disabled", String(button.disabled));
}

function pricingReferenceEditBlockReason(reference = deletionPricingReference()) {
  if (!reference) return "Select a pricing reference first.";
  if (!canManagePricingReferences()) return pricingReferenceNoAccessReason();
  if (String(reference.source || "bundled") !== "local") return "Only local pricing reference packs can be edited here.";
  const knownItemCount = Array.isArray(reference.items) ? reference.items.length : Number(reference.item_count);
  if (Number.isFinite(knownItemCount) && knownItemCount <= 0) return "This pricing reference has no editable rows.";
  return "";
}

function canEditSelectedPricingReference() {
  return !pricingReferenceEditBlockReason(deletionPricingReference());
}

function normalizePricingReferenceSettingsMode(value = "") {
  return String(value || "").trim().toLowerCase() === PRICING_REFERENCE_SETTINGS_MODE_IMPORT
    ? PRICING_REFERENCE_SETTINGS_MODE_IMPORT
    : PRICING_REFERENCE_SETTINGS_MODE_MANAGE;
}

function pricingReferenceEditStatusText(result = state.pendingPricingReference) {
  if (!result || !state.editingPricingReferenceId) return "";
  const rowCount = Array.isArray(result.items) ? result.items.length : Number(result.rowCount || 0);
  const issues = pricingReferenceRowIssues(result.items || []);
  if (issues.length) {
    return `${rowCount} row${rowCount === 1 ? "" : "s"} loaded. ${issues.length} need edit before saving.`;
  }
  return `${rowCount} row${rowCount === 1 ? "" : "s"} loaded and ready to save after review.`;
}

function pricingReferenceSaveProgressMarkup() {
  return `
    <div class="pricing-reference-import-overlay" role="status" aria-live="polite">
      <span class="pricing-reference-spinner" aria-hidden="true"></span>
      <strong>Saving pricing reference</strong>
      <p>Please wait while reviewed rows are validated and saved.</p>
      <span class="ai-elapsed pricing-reference-save-elapsed" data-elapsed-timer-id="pricingReferenceSaveElapsed" aria-live="polite">Elapsed 0:00</span>
    </div>
  `;
}

function renderPricingReferenceManageStatus(result = state.pendingPricingReference) {
  const status = elements.pricingReferenceManageStatus;
  if (!status) return;
  const resultErrors = Array.isArray(result?.errors) ? result.errors.filter(Boolean) : [];
  if (!result || (!state.editingPricingReferenceId && !resultErrors.length)) {
    status.hidden = true;
    status.innerHTML = "";
    return;
  }
  const label = elements.pricingReferenceName?.value || result.sourceName || state.editingPricingReferenceId || "Pricing reference";
  if (String(result.layout || "") === "loading-pricing-reference") {
    status.hidden = false;
    status.className = "pricing-reference-manage-status is-warn";
    status.innerHTML = `
      <div>
        <span>Loading reference</span>
        <strong>${escapeHtml(label)}</strong>
        <p>Loading editable pricing rows...</p>
      </div>
    `;
    return;
  }
  if (state.pricingReferenceSaveBusy) {
    status.hidden = false;
    status.className = "pricing-reference-manage-status is-saving";
    status.innerHTML = pricingReferenceSaveProgressMarkup();
    return;
  }
  const canSave = Boolean(result.canSave) && !pricingReferenceSaveBlockReason(result);
  const hasChanges = pricingReferenceHasPendingChanges(result);
  const savedNotice = String(state.pricingReferenceSavedNotice || "").trim();
  const editNotice = String(state.pricingReferenceEditNotice || "").trim();
  const rowIssues = pricingReferenceRowIssues(result.items || []);
  const noUnsavedChanges = !savedNotice && !editNotice && !hasChanges;
  const rowCount = Array.isArray(result.items) ? result.items.length : Number(result.rowCount || 0);
  const blockingStatusText = rowIssues.length
    ? `${rowIssues.length} pricing row${rowIssues.length === 1 ? "" : "s"} still need edit before saving.`
    : resultErrors.length
      ? resultErrors.join(" ")
      : rowCount <= 0
        ? "Add at least one valid pricing row before saving."
        : "";
  const statusText = !state.editingPricingReferenceId && blockingStatusText
    ? blockingStatusText
    : !savedNotice && editNotice && blockingStatusText
      ? `${editNotice} ${blockingStatusText}`
      : savedNotice || editNotice || (hasChanges ? pricingReferenceEditStatusText(result) : "No unsaved changes.");
  const hasBlockingIssues = rowIssues.length > 0
    || resultErrors.length > 0
    || rowCount <= 0;
  const isBlocked = hasBlockingIssues && !noUnsavedChanges && !savedNotice;
  const isReady = !hasBlockingIssues && Boolean(savedNotice);
  const isWarn = !isBlocked && !isReady;
  status.hidden = false;
  status.classList.remove("is-saving");
  status.classList.toggle("is-ready", isReady);
  status.classList.toggle("is-warn", isWarn);
  status.classList.toggle("is-blocked", isBlocked);
  status.classList.toggle("is-saved", Boolean(savedNotice));
  status.innerHTML = `
    <div>
      <span>${savedNotice ? "Saved reference" : state.editingPricingReferenceId ? "Editing reference" : "Reference unavailable"}</span>
      <strong>${escapeHtml(label)}</strong>
      <p>${escapeHtml(sentenceLineBreakText(statusText))}</p>
    </div>
    ${rowCount > 0 ? '<button class="secondary-button pricing-reference-table-open" type="button" data-pricing-reference-table-open>Review</button>' : ""}
  `;
}

function pricingReferenceReviewReadyForMetadata(result = state.pendingPricingReference) {
  if (!result || state.pricingReferenceImportBusy || state.editingPricingReferenceId) return false;
  const rowCount = Array.isArray(result.items) ? result.items.length : Number(result.rowCount || 0);
  const errors = Array.isArray(result.errors) ? result.errors.filter(Boolean) : [];
  return Boolean(result.canSave)
    && rowCount > 0
    && errors.length === 0
    && pricingReferenceRowIssues(result.items || []).length === 0;
}

function syncPricingReferenceImportSetupVisibility() {
  if (!elements.pricingReferenceImportSetup) return;
  const mode = normalizePricingReferenceSettingsMode(state.pricingReferenceSettingsMode);
  const hasImportDraft = !state.editingPricingReferenceId && Boolean(state.pendingPricingReference);
  const reviewReady = hasImportDraft && pricingReferenceReviewReadyForMetadata(state.pendingPricingReference);
  const showImportSetup = mode === PRICING_REFERENCE_SETTINGS_MODE_IMPORT
    && (state.pricingReferenceImportFileSelected || state.pricingReferenceImportBusy || hasImportDraft);
  const showMetadataSetup = showImportSetup
    && !state.pricingReferenceImportBusy
    && hasImportDraft
    && reviewReady
    && String(state.pendingPricingReference?.layout || "") !== "importing";
  elements.pricingReferenceImportSetup.hidden = !showImportSetup;
  if (elements.pricingReferenceMetadataSetup) elements.pricingReferenceMetadataSetup.hidden = !showMetadataSetup;
}

function syncPricingReferenceSettingsMode() {
  const mode = normalizePricingReferenceSettingsMode(state.pricingReferenceSettingsMode);
  state.pricingReferenceSettingsMode = mode;
  const tabEntries = [
    [elements.pricingReferenceManageTab, PRICING_REFERENCE_SETTINGS_MODE_MANAGE],
    [elements.pricingReferenceImportTab, PRICING_REFERENCE_SETTINGS_MODE_IMPORT],
  ];
  tabEntries.forEach(([tab, tabMode]) => {
    if (!tab) return;
    const active = tabMode === mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  const panelEntries = [
    [elements.pricingReferenceManagePanel, PRICING_REFERENCE_SETTINGS_MODE_MANAGE],
    [elements.pricingReferenceImportPanel, PRICING_REFERENCE_SETTINGS_MODE_IMPORT],
  ];
  panelEntries.forEach(([panel, panelMode]) => {
    if (!panel) return;
    panel.hidden = panelMode !== mode;
  });
  if (elements.pricingReferenceTemplateFooter) {
    elements.pricingReferenceTemplateFooter.hidden = false;
    elements.pricingReferenceTemplateFooter.classList.remove("is-placeholder");
    elements.pricingReferenceTemplateFooter.setAttribute("aria-hidden", "false");
  }
  if (elements.pricingReferenceTemplateButton) {
    elements.pricingReferenceTemplateButton.hidden = mode !== PRICING_REFERENCE_SETTINGS_MODE_IMPORT;
  }
  if (typeof updatePricingReferenceExportButton === "function") updatePricingReferenceExportButton();
  syncPricingReferenceImportSetupVisibility();
  renderPricingReferenceManageStatus();
}

function setPricingReferenceSettingsMode(mode = PRICING_REFERENCE_SETTINGS_MODE_MANAGE, options = {}) {
  state.pricingReferenceSettingsMode = normalizePricingReferenceSettingsMode(mode);
  if (state.pricingReferenceSettingsMode !== PRICING_REFERENCE_SETTINGS_MODE_MANAGE) {
    hidePricingReferenceDeleteConfirm();
  }
  syncPricingReferenceSettingsMode();
  if (state.restorableOverlay === "pricing_reference_settings") saveSessionState();
  if (options.focus) {
    const target = state.pricingReferenceSettingsMode === PRICING_REFERENCE_SETTINGS_MODE_IMPORT
      ? elements.pricingReferenceFile
      : elements.deletePricingReferenceSelect;
    window.setTimeout(() => target?.focus(), 0);
  }
}

function handlePricingReferenceImportTabClick() {
  setPricingReferenceSettingsMode(PRICING_REFERENCE_SETTINGS_MODE_IMPORT, { focus: true });
}

function clearPricingReferenceDraft(options = {}) {
  closePricingReferenceTableOverlay();
  stopElapsedTimer("pricingReferenceImportElapsed");
  stopElapsedTimer("pricingReferenceSaveElapsed");
  state.pendingPricingReference = null;
  state.editingPricingReferenceId = "";
  state.editingPricingReferenceSource = "";
  state.pricingReferenceImportFileSelected = false;
  state.pricingReferenceImportBusy = false;
  state.pricingReferenceSaveBusy = false;
  state.pricingReferenceImportToken = "";
  state.pricingReferenceEditSnapshot = "";
  state.pricingReferenceEditUndoStack = [];
  state.pricingReferenceEditPendingUndoSnapshot = "";
  state.pricingReferenceTableOpenedSnapshot = "";
  state.pricingReferenceEditNotice = "";
  state.pricingReferenceAutoLoadToken = "";
  state.pricingReferenceSavedNotice = "";
  hidePricingReferenceDeleteConfirm();
  if (options.clearFile) {
    if (elements.pricingReferenceFile) elements.pricingReferenceFile.value = "";
    if (elements.pricingReferenceFileName) elements.pricingReferenceFileName.textContent = "No file chosen";
  }
  if (options.resetMetadata) {
    if (elements.pricingReferenceName) elements.pricingReferenceName.value = "";
    resetPricingReferenceTaxInputs();
  }
  renderPricingReferencePreview(null);
}

function renderPricingReferenceDeleteOptions() {
  const select = elements.deletePricingReferenceSelect;
  if (!select) return;
  const previousValue = select.value;
  const references = sortedPricingReferencesForDisplay(
    state.pricingReferences.filter((reference) => ["local", "bundled"].includes(String(reference?.source || "bundled")))
  );
  select.innerHTML = references.map((reference) => `
    <option value="${escapeHtml(pricingReferenceSelectValue(reference))}">${escapeHtml(reference.label || reference.id || "Pricing reference")}</option>
  `).join("");
  const preferred = references.find((reference) => pricingReferenceSelectValue(reference) === previousValue)
    || references.find((reference) => !protectedPricingReferenceReason(reference))
    || references[0]
    || null;
  select.value = preferred ? pricingReferenceSelectValue(preferred) : "";
  updatePricingReferenceDeleteButton();
}

function updatePricingReferenceDeleteButton() {
  const button = elements.deletePricingReferenceButton;
  if (elements.pricingReferenceDeleteSection) {
    elements.pricingReferenceDeleteSection.hidden = !canManagePricingReferences();
  }
  if (!button) return;
  const reference = deletionPricingReference();
  const reason = protectedPricingReferenceReason(reference);
  const busy = pricingReferenceOperationBusy();
  button.disabled = Boolean(reason) || busy;
  button.title = busy ? "Pricing reference operation is still running." : reason || "Delete this local pricing reference.";
  button.setAttribute("aria-disabled", String(button.disabled));
  if (typeof updatePricingReferenceExportButton === "function") updatePricingReferenceExportButton();
}

function pricingReferenceDeleteConfirmReference() {
  const referenceId = String(state.pricingReferenceDeleteConfirmId || "").trim();
  const source = String(state.pricingReferenceDeleteConfirmSource || "local").trim() || "local";
  if (!referenceId) return null;
  return state.pricingReferences.find((reference) => reference.id === referenceId && String(reference.source || "bundled") === source) || null;
}

function hidePricingReferenceDeleteConfirm() {
  state.pricingReferenceDeleteConfirmId = "";
  state.pricingReferenceDeleteConfirmSource = "";
  state.pricingReferenceDeleteError = "";
  if (elements.pricingReferenceDeleteConfirm) elements.pricingReferenceDeleteConfirm.hidden = true;
  if (elements.pricingReferenceDeleteError) {
    elements.pricingReferenceDeleteError.hidden = true;
    elements.pricingReferenceDeleteError.textContent = "";
  }
}

function renderPricingReferenceDeleteConfirm() {
  const panel = elements.pricingReferenceDeleteConfirm;
  if (!panel) return;
  const reference = pricingReferenceDeleteConfirmReference();
  if (!reference) {
    panel.hidden = true;
    return;
  }
  const label = reference.label || reference.id || "this pricing reference";
  panel.hidden = false;
  if (elements.pricingReferenceDeleteConfirmTitle) {
    elements.pricingReferenceDeleteConfirmTitle.textContent = `Delete ${label}?`;
  }
  if (elements.pricingReferenceDeleteConfirmText) {
    elements.pricingReferenceDeleteConfirmText.textContent = "This removes the saved local pricing reference pack from this app. Existing quotes already generated from it are not changed.";
  }
  if (elements.pricingReferenceDeleteError) {
    const message = String(state.pricingReferenceDeleteError || "").trim();
    elements.pricingReferenceDeleteError.hidden = !message;
    elements.pricingReferenceDeleteError.textContent = message;
  }
  [elements.cancelPricingReferenceDeleteButton, elements.confirmPricingReferenceDeleteButton].forEach((button) => {
    if (!button) return;
    button.disabled = state.pricingReferenceDeleteBusy;
    button.setAttribute("aria-disabled", String(button.disabled));
  });
  if (elements.confirmPricingReferenceDeleteButton) {
    elements.confirmPricingReferenceDeleteButton.textContent = state.pricingReferenceDeleteBusy ? "Deleting..." : "Delete";
  }
}

function showPricingReferenceDeleteConfirm(reference) {
  if (!reference) return;
  state.pricingReferenceDeleteConfirmId = reference.id || "";
  state.pricingReferenceDeleteConfirmSource = reference.source || "local";
  state.pricingReferenceDeleteError = "";
  renderPricingReferenceDeleteConfirm();
  queueActionButtonFocus(elements.confirmPricingReferenceDeleteButton);
}

function openSettingsModal() {
  if (!canManagePricingReferences()) return;
  openPricingReferenceModal();
}

function pricingReferenceRequiredColumns() {
  return ["section", "description", "unit_hint", "internal_cost", "markup_multiplier"];
}

const PRICING_REFERENCE_PREVIEW_FIELDS = [
  "section",
  "description",
  "unit_hint",
  "internal_cost",
  "markup_multiplier",
  "remarks",
];

const PRICING_REFERENCE_PREVIEW_PLACEHOLDERS = {
  section: "e.g. AV equipment",
  description: "e.g. LED screen, truss, projector, chairs",
  unit_hint: "e.g. day",
  internal_cost: "e.g. 150.00",
  markup_multiplier: "e.g. 1.25",
  remarks: "e.g. Delivery excluded",
};

const PRICING_REFERENCE_METADATA_STALE_FIELDS = new Set([
  "section",
  "description",
  "unit_hint",
  "remarks",
]);

function pricingReferenceOperationBusy() {
  return Boolean(state.pricingReferenceImportBusy || state.pricingReferenceSaveBusy || state.pricingReferenceDeleteBusy);
}

function pricingReferenceSnapshotItem(item = {}) {
  return {
    section: normalizeCategoryTitle(item.section || item.reference_section || ""),
    description: cleanCustomerQuoteLineText(item.description || ""),
    unit_hint: normalizeUnit(item.unit_hint || item.unit || ""),
    internal_cost: numberOrNull(item.internal_cost ?? item.cost),
    markup_multiplier: numberOrNull(item.markup_multiplier ?? item.markup),
    remarks: String(item.remarks || ""),
    category_order: orderNumber(item.category_order) ?? "",
    item_order: orderNumber(item.item_order) ?? "",
  };
}

function pricingReferenceSnapshot(result = state.pendingPricingReference) {
  const items = Array.isArray(result?.items) ? result.items.map(pricingReferenceSnapshotItem) : [];
  return JSON.stringify({
    id: state.editingPricingReferenceId || "",
    label: elements.pricingReferenceName?.value?.trim() || "",
    tax: pricingReferenceModalTax(),
    currency: pricingReferenceModalCurrency(),
    items,
  });
}

function capturePricingReferenceEditSnapshot(result = state.pendingPricingReference) {
  state.pricingReferenceEditSnapshot = pricingReferenceSnapshot(result);
  state.pricingReferenceSavedNotice = "";
  state.pricingReferenceEditNotice = "";
  state.pricingReferenceEditUndoStack = [];
  state.pricingReferenceEditPendingUndoSnapshot = "";
  updatePricingReferenceUndoButton();
}

function pricingReferenceHasPendingChanges(result = state.pendingPricingReference) {
  if (!result) return false;
  if (!state.editingPricingReferenceId) return true;
  return pricingReferenceSnapshot(result) !== state.pricingReferenceEditSnapshot;
}

function pricingReferenceDraftUndoSnapshot(result = state.pendingPricingReference) {
  return JSON.stringify({
    items: Array.isArray(result?.items) ? result.items.map(pricingReferenceSnapshotItem) : [],
  });
}

function restorePricingReferenceDraftUndoSnapshot(snapshot = "") {
  if (!snapshot || !state.pendingPricingReference) return false;
  let parsed;
  try {
    parsed = JSON.parse(snapshot);
  } catch {
    return false;
  }
  state.pendingPricingReference.items = Array.isArray(parsed.items)
    ? parsed.items.map(normalizePricingReferencePreviewItem)
    : [];
  state.pricingReferenceSavedNotice = "";
  state.pricingReferenceEditNotice = "Undid previous row edit.";
  refreshPricingReferencePreviewValidity(state.pendingPricingReference);
  renderPricingReferencePreview(state.pendingPricingReference);
  renderPricingReferenceTableOverlay(state.pendingPricingReference);
  renderPricingReferenceManageStatus(state.pendingPricingReference);
  updatePricingReferenceUndoButton();
  return true;
}

function pushPricingReferenceUndoSnapshot(snapshot = pricingReferenceDraftUndoSnapshot()) {
  if (!snapshot) return;
  const stack = Array.isArray(state.pricingReferenceEditUndoStack) ? state.pricingReferenceEditUndoStack : [];
  if (stack[stack.length - 1] !== snapshot) stack.push(snapshot);
  state.pricingReferenceEditUndoStack = stack;
  updatePricingReferenceUndoButton();
}

function updatePricingReferenceUndoButton() {
  const button = elements.pricingReferenceUndoButton;
  if (!button) return;
  const undoCount = Array.isArray(state.pricingReferenceEditUndoStack) ? state.pricingReferenceEditUndoStack.length : 0;
  button.disabled = undoCount <= 0 || pricingReferenceOperationBusy();
  button.textContent = undoCount > 0 ? `Undo (${undoCount})` : "Undo";
  button.title = undoCount > 0 ? "Undo the previous pricing-row edit." : "No row edits to undo.";
  button.setAttribute("aria-disabled", String(button.disabled));
}

function undoPricingReferenceTableEdit() {
  const stack = Array.isArray(state.pricingReferenceEditUndoStack) ? state.pricingReferenceEditUndoStack : [];
  const snapshot = stack.pop();
  state.pricingReferenceEditUndoStack = stack;
  restorePricingReferenceDraftUndoSnapshot(snapshot);
}

function pricingReferenceItemChangeCount(beforeSnapshot = "", result = state.pendingPricingReference) {
  if (!beforeSnapshot || !result) return 0;
  let before;
  try {
    before = JSON.parse(beforeSnapshot);
  } catch {
    return 0;
  }
  const beforeItems = Array.isArray(before.items) ? before.items : [];
  const afterItems = Array.isArray(result.items) ? result.items.map(pricingReferenceSnapshotItem) : [];
  const maxLength = Math.max(beforeItems.length, afterItems.length);
  let count = 0;
  for (let index = 0; index < maxLength; index += 1) {
    if (JSON.stringify(beforeItems[index] || null) !== JSON.stringify(afterItems[index] || null)) count += 1;
  }
  return count;
}

function markPricingReferenceDraftChanged() {
  state.pricingReferenceSavedNotice = "";
  state.pricingReferenceEditNotice = "";
  if (state.pendingPricingReference) {
    renderPricingReferencePreview(state.pendingPricingReference);
    return;
  }
  setPricingReferenceSaveButtonState({
    canSave: Boolean(state.pendingPricingReference?.canSave),
    busy: pricingReferenceOperationBusy(),
    reason: pricingReferenceSaveBlockReason(state.pendingPricingReference),
  });
  renderPricingReferenceManageStatus(state.pendingPricingReference);
}

function pricingReferenceNameId(value = "") {
  return safeId(value, "");
}

function pricingReferenceById(referenceId = "") {
  const normalizedId = String(referenceId || "").trim();
  if (!normalizedId) return null;
  return (Array.isArray(state.pricingReferences) ? state.pricingReferences : [])
    .find((reference) => String(reference?.id || "").trim() === normalizedId) || null;
}

function pricingReferenceImportNameConflict(name = elements.pricingReferenceName?.value || "") {
  const mode = normalizePricingReferenceSettingsMode(state.pricingReferenceSettingsMode);
  if (mode !== PRICING_REFERENCE_SETTINGS_MODE_IMPORT || state.editingPricingReferenceId) return null;
  return pricingReferenceById(pricingReferenceNameId(name));
}

function pricingReferenceImportNameConflictMessage(name = elements.pricingReferenceName?.value || "") {
  const conflict = pricingReferenceImportNameConflict(name);
  if (!conflict) return "";
  const label = conflict.label || conflict.id || name;
  return `A pricing reference named "${label}" already exists. Choose a different pricing reference name, or switch to Manage to edit it.`;
}

function pricingReferenceDuplicateRowKey(item = {}) {
  const section = normalizeCategoryTitle(item.section || item.reference_section || "").trim().toLowerCase();
  const description = cleanCustomerQuoteLineText(item.description || "").trim().toLowerCase();
  const unitHint = normalizeUnit(item.unit_hint || item.unit || "").trim().toLowerCase();
  const internalCost = numberOrNull(item.internal_cost ?? item.cost);
  const markupMultiplier = numberOrNull(item.markup_multiplier ?? item.markup);
  const remarks = String(item.remarks || "").trim().toLowerCase().replace(/\s+/g, " ");
  if (!section && !description && !unitHint && internalCost === null && markupMultiplier === null && !remarks) return "";
  return [
    section,
    description,
    unitHint,
    internalCost === null ? "" : String(internalCost),
    markupMultiplier === null ? "" : String(markupMultiplier),
    remarks,
  ].join("\u001f");
}

function pricingReferenceDuplicateMarkers(items = []) {
  const ids = new Set();
  const rowKeys = new Set();
  const duplicateIds = new Set();
  const duplicateRowKeys = new Set();
  items.forEach((item) => {
    const id = String(item?.id || "").trim();
    if (id) {
      if (ids.has(id)) duplicateIds.add(id);
      ids.add(id);
    }
    const rowKey = pricingReferenceDuplicateRowKey(item);
    if (rowKey) {
      if (rowKeys.has(rowKey)) duplicateRowKeys.add(rowKey);
      rowKeys.add(rowKey);
    }
  });
  return { duplicateIds, duplicateRowKeys };
}

function pricingReferencePreviewFromReference(reference = {}) {
  const items = sortPricingReferencePreviewItems((Array.isArray(reference.items) ? reference.items : [])
    .map(normalizePricingReferencePreviewItem));
  const { duplicateIds, duplicateRowKeys } = pricingReferenceDuplicateMarkers(items);
  const invalid = items.flatMap((item) => {
    const warnings = pricingReferenceRowStatus(item);
    if (duplicateIds.has(item.id)) warnings.push("duplicate id");
    if (duplicateRowKeys.has(pricingReferenceDuplicateRowKey(item))) warnings.push("duplicate pricing row");
    item.warning = warnings.join("; ") || "OK";
    return warnings;
  });
  return {
    referenceId: reference.id || "",
    sourceName: reference.label || reference.id || "Pricing reference",
    description: reference.description || "",
    items,
    rowCount: items.length,
    headers: PRICING_REFERENCE_PREVIEW_FIELDS,
    missing: [],
    skipped: 0,
    layout: "saved-pricing-reference",
    errors: [],
    warnings: [],
    canSave: !invalid.length && items.length > 0,
    tax: {
      label: normalizeTaxLabel(reference.tax?.label),
      rate: normalizeTaxRate(reference.tax?.rate, DEFAULT_TAX_RATE),
    },
    currency: normalizeCurrencyLabel(reference.currency),
  };
}

async function fetchPricingReferenceDetail(reference = {}) {
  const referenceId = String(reference.id || "").trim();
  if (!referenceId) return null;
  const source = String(reference.source || "").trim();
  const query = source ? `?source=${encodeURIComponent(source)}` : "";
  const { ok, data } = await getJson(`/api/settings/pricing-references/${encodeURIComponent(referenceId)}${query}`);
  if (!ok || !data || typeof data.pricing_reference !== "object") {
    return null;
  }
  return {
    ...reference,
    ...data.pricing_reference,
    source: reference.source || data.pricing_reference.source || "bundled",
  };
}

function normalizeBasisTag(tag = "") {
  const normalized = String(tag || "").trim().toLowerCase();
  if (normalized === "include" || normalized === "matched") return "Include";
  if (["custom", "manual", "extra", "non-catalog", "non catalog", "needs-pricing", "needs pricing"].includes(normalized)) return "Custom";
  if (normalized === "exclude") return "Exclude";
  return "Confirm";
}

function isCustomPricingBasisLine(line = {}) {
  return normalizeBasisTag(line.tag) === "Custom"
    || Boolean(line.custom_pricing || line.custom || line.manual_pricing)
    || normalizeBasisTag(line.pricing_tag || line.pricing_status) === "Custom";
}

function isPendingAiProposalLine(line = {}) {
  const tag = normalizeBasisTag(line.tag);
  return isCustomPricingBasisLine(line) && (tag === "Confirm" || (tag === "Custom" && !line.custom_confirmed));
}

function normalizeConfidence(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(String(value).replace("%", "").trim());
  if (!Number.isFinite(number)) return null;
  return Math.min(100, Math.max(0, Math.round(number)));
}

function normalizePossiblePricingMatches(value = []) {
  if (!Array.isArray(value)) return [];
  const matches = [];
  const seen = new Set();
  value.forEach((entry) => {
    if (!entry || typeof entry !== "object") return;
    const pricingKeyword = String(entry.pricing_keyword || entry.id || "").trim();
    const description = cleanCustomerQuoteLineText(entry.description || entry.pricing_reference_description || entry.catalog_description || "");
    if (!pricingKeyword || !description || seen.has(pricingKeyword)) return;
    seen.add(pricingKeyword);
    const match = {
      pricing_keyword: pricingKeyword,
      description,
      section: normalizeCategoryTitle(entry.section || entry.reference_section || ""),
      unit: normalizeUnit(entry.unit || ""),
    };
    if (entry.catalog_unit_price !== undefined && entry.catalog_unit_price !== null && String(entry.catalog_unit_price).trim() !== "") {
      match.catalog_unit_price = entry.catalog_unit_price;
    }
    const score = Number(entry.score);
    if (Number.isFinite(score)) match.score = score;
    matches.push(match);
  });
  return matches;
}

function splitBasisDecisionText(text = "", defaultTag = "Confirm") {
  const legacyConfirmTag = "assump" + "tion";
  const raw = String(text || "").trim();
  if (!raw) return [];
  const pattern = `Include|Confirm|Custom|Manual|Extra|Needs Pricing|Exclude|matched|${legacyConfirmTag}|Note`;
  const expression = new RegExp(`(?:^|[;\\n]\\s*)(${pattern}):\\s*`, "gi");
  const matches = Array.from(raw.matchAll(expression));
  if (!matches.length) return [{ tag: normalizeBasisTag(defaultTag), text: raw }];

  const lines = [];
  const firstStart = matches[0].index || 0;
  const leading = raw.slice(0, firstStart).replace(/[;\s]+$/g, "").trim();
  if (leading) lines.push({ tag: normalizeBasisTag(defaultTag), text: leading });
  matches.forEach((match, index) => {
    const nextStart = index + 1 < matches.length ? matches[index + 1].index : raw.length;
    const segment = raw.slice(match.index + match[0].length, nextStart).replace(/^[;\s]+|[;\s]+$/g, "").trim();
    if (segment) lines.push({ tag: normalizeBasisTag(match[1]), text: segment });
  });
  return lines;
}

function normalizeBasisLines(line = "") {
  if (line && typeof line === "object") {
    const quantityParts = normalizedLineTextQuantityParts(
      line.text || line.line || line.description || "",
      line.quantity,
      line.unit
    );
    const text = quantityParts.text;
    if (!text) return [];
    const confidence = normalizeConfidence(line.confidence ?? line.confidence_pct);
    const hasCustomPricing = isCustomPricingBasisLine(line);
    return splitBasisDecisionText(text, line.tag).map((parsed) => {
      const next = { tag: normalizeBasisTag(parsed.tag), text: parsed.text };
      if (line.id) next.id = String(line.id);
      if (line.source_line_item_id) next.source_line_item_id = String(line.source_line_item_id);
      if (line.pricing_keyword) next.pricing_keyword = String(line.pricing_keyword);
      if (line.catalog_unit_price !== undefined && line.catalog_unit_price !== null && String(line.catalog_unit_price).trim() !== "") next.catalog_unit_price = line.catalog_unit_price;
      if (line.catalog_description) next.catalog_description = cleanCustomerQuoteLineText(line.catalog_description);
      if (line.pricing_reference_description) next.pricing_reference_description = pricingReferenceLineText(line.pricing_reference_description);
      if (orderNumber(line.category_order) !== null) next.category_order = orderNumber(line.category_order);
      if (orderNumber(line.item_order) !== null) next.item_order = orderNumber(line.item_order);
      if (quantityParts.quantity !== undefined && quantityParts.quantity !== null && String(quantityParts.quantity).trim() !== "") next.quantity = quantityParts.quantity;
      if (quantityParts.unit) next.unit = quantityParts.unit;
      if (confidence !== null) next.confidence = confidence;
      if (hasCustomPricing || normalizeBasisTag(parsed.tag) === "Custom") next.custom_pricing = true;
      if (line.custom_confirmed) next.custom_confirmed = true;
      const possibleMatches = normalizePossiblePricingMatches(line.possible_pricing_matches || line.possible_catalog_matches || []);
      if (possibleMatches.length) next.possible_pricing_matches = possibleMatches;
      return next;
    });
  }
  return splitBasisDecisionText(cleanCustomerQuoteLineText(line));
}

function parseBasisLine(line = "") {
  return normalizeBasisLines(line)[0] || { tag: "Confirm", text: "" };
}

function normalizeQuoteBasisSections(value = {}) {
  const rawSections = Array.isArray(value)
    ? value
    : Array.isArray(value.quote_basis_sections)
      ? value.quote_basis_sections
      : null;
  if (rawSections) {
    return rawSections
      .map((section, index) => {
        const title = normalizeQuoteBasisTitle(section?.title || "Section") || "Section";
        const id = safeId(section?.id && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(String(section.id)) ? section.id : title, `section-${index + 1}`);
        const rawLines = Array.isArray(section?.lines) ? section.lines : splitLines(section?.text || "");
        const lines = rawLines.flatMap(normalizeBasisLines).filter((line) => line.text);
        return lines.length ? { id, title, lines } : null;
      })
      .filter(Boolean);
  }
  const basis = value.quote_basis && typeof value.quote_basis === "object" ? value.quote_basis : value;
  return BASIS_FIELDS
    .map(([key, title]) => {
      const lines = splitLines(basis[key])
        .flatMap(normalizeBasisLines)
        .filter((line) => line.text);
      return lines.length ? { id: key, title, lines } : null;
    })
    .filter(Boolean);
}

function confirmOnlyQuoteBasisSections(sections = []) {
  return normalizeQuoteBasisSections(sections).map((section) => ({
    ...section,
    lines: (section.lines || []).map((line) => {
      const tag = normalizeBasisTag(line.tag);
      return { ...line, tag: tag === "Custom" ? "Custom" : "Confirm" };
    }),
  }));
}

function quoteBasisFromSections(sections = []) {
  return (Array.isArray(sections) ? sections : []).reduce((basis, section) => {
    const id = safeId(section.id || section.title, "section");
    basis[id] = (section.lines || [])
      .map((line) => `${normalizeBasisTag(line.tag)}: ${line.text || ""}`.trim())
      .filter((line) => !/:\s*$/.test(line))
      .join("\n");
    return basis;
  }, {});
}

function cloneQuoteBasisSections(sections = []) {
  return normalizeQuoteBasisSections(JSON.parse(JSON.stringify(Array.isArray(sections) ? sections : [])));
}

function basisLineMetadataMergeKey(line = {}) {
  const id = String(line.id || "").trim();
  if (id) return `id:${id}`;
  const sourceId = String(line.source_line_item_id || "").trim();
  if (sourceId) return `source:${sourceId}`;
  return "";
}

function basisLineCoreMatches(left = {}, right = {}) {
  return normalizeBasisTag(left.tag) === normalizeBasisTag(right.tag)
    && cleanCustomerQuoteLineText(left.text || "") === cleanCustomerQuoteLineText(right.text || "")
    && String(left.quantity ?? "") === String(right.quantity ?? "")
    && normalizeUnit(left.unit || "") === normalizeUnit(right.unit || "");
}

function mergeBasisProposalLineMetadata(nextSections = [], currentSections = []) {
  const currentBySectionId = new Map();
  const currentBySectionTitle = new Map();
  (Array.isArray(currentSections) ? currentSections : []).forEach((section) => {
    if (!section || typeof section !== "object") return;
    const sectionId = String(section.id || "").trim();
    if (sectionId) currentBySectionId.set(sectionId, section);
    const titleKey = basisDisplayTitle(section.title || "").toLowerCase();
    if (titleKey) currentBySectionTitle.set(titleKey, section);
  });
  return (Array.isArray(nextSections) ? nextSections : []).map((section) => {
    const currentSection = currentBySectionId.get(String(section?.id || "").trim())
      || currentBySectionTitle.get(basisDisplayTitle(section?.title || "").toLowerCase())
      || null;
    if (!currentSection) return section;
    const currentLines = Array.isArray(currentSection.lines) ? currentSection.lines : [];
    const currentByLineKey = new Map();
    currentLines.forEach((line) => {
      const key = basisLineMetadataMergeKey(line);
      if (key) currentByLineKey.set(key, line);
    });
    return {
      ...section,
      lines: (Array.isArray(section.lines) ? section.lines : []).map((line, index) => {
        const key = basisLineMetadataMergeKey(line);
        const currentLine = (key && currentByLineKey.get(key)) || currentLines[index] || null;
        if (!currentLine || !basisLineCoreMatches(currentLine, line)) return line;
        return { ...currentLine, ...line };
      }),
    };
  });
}

function reviewBasisProposalSections(nextSections = [], currentSections = []) {
  const currentBySectionId = new Map();
  const currentBySectionTitle = new Map();
  (Array.isArray(currentSections) ? currentSections : []).forEach((section) => {
    if (!section || typeof section !== "object") return;
    const sectionId = String(section.id || "").trim();
    if (sectionId) currentBySectionId.set(sectionId, section);
    const titleKey = basisDisplayTitle(section.title || "").toLowerCase();
    if (titleKey) currentBySectionTitle.set(titleKey, section);
  });
  return (Array.isArray(nextSections) ? nextSections : []).map((section) => {
    const currentSection = currentBySectionId.get(String(section?.id || "").trim())
      || currentBySectionTitle.get(basisDisplayTitle(section?.title || "").toLowerCase())
      || null;
    const currentLines = Array.isArray(currentSection?.lines) ? currentSection.lines : [];
    const currentByLineKey = new Map();
    currentLines.forEach((line) => {
      const key = basisLineMetadataMergeKey(line);
      if (key) currentByLineKey.set(key, line);
    });
    return {
      ...section,
      lines: (Array.isArray(section.lines) ? section.lines : []).map((line, index) => {
        const key = basisLineMetadataMergeKey(line);
        const currentLine = (key && currentByLineKey.get(key)) || currentLines[index] || null;
        if (currentLine && basisLineCoreMatches(currentLine, line)) return line;
        const tag = normalizeBasisTag(line.tag);
        const customPricing = isCustomPricingBasisLine(line);
        if (tag === "Include") {
          return {
            ...line,
            tag: customPricing ? "Custom" : "Confirm",
            ...(customPricing ? { custom_pricing: true, custom_confirmed: false } : {}),
          };
        }
        if (tag === "Custom") {
          return { ...line, custom_pricing: true, custom_confirmed: false };
        }
        return line;
      }),
    };
  });
}

function normalizeOutputRow(row = {}) {
  const priceMode = row.price_mode === "Included" || String(row.display_price || "").toLowerCase() === "included"
    ? "Included"
    : "Priced";
  const currentDescription = cleanCustomerQuoteLineText(row.description || "");
  const trustedCatalogSource = pricingReferenceLineText(
    row.pricing_reference_description || row.catalog_description || ""
  );
  const catalogDescription = row.pricing_keyword && trustedCatalogSource
    ? outputCatalogDescription({ ...row, description: "", text: "" })
    : "";
  const legacyWrappedDescription = bracketedCatalogReferenceParts(row.description || "");
  const description = !currentDescription
    ? catalogDescription
    : (
      catalogDescription
      && legacyWrappedDescription?.reference === catalogDescription
      && !legacyWrappedDescription.detail
        ? catalogDescription
        : currentDescription
    );
  return recalculateOutputRow({
    section: normalizeCategoryTitle(row.section || ""),
    description,
    quantity: row.quantity ?? "",
    unit: normalizeUnit(row.unit || ""),
    price_mode: priceMode,
    unit_price_override: row.unit_price_override ?? "",
    catalog_unit_price: row.catalog_unit_price ?? row.unit_price ?? row.sale_unit_price ?? "",
    catalog_description: cleanCustomerQuoteLineText(row.catalog_description || ""),
    pricing_reference_description: pricingReferenceLineText(row.pricing_reference_description || row.reference_description || ""),
    pricing_keyword: row.pricing_keyword || row.keyword || "",
    source_basis_line_id: row.source_basis_line_id || "",
    category_order: orderNumber(row.category_order) ?? "",
    item_order: orderNumber(row.item_order) ?? "",
    basis_order: orderNumber(row.basis_order) ?? "",
    status: row.status || "",
  });
}

function pricingReferenceValidationResult(items, headers, skipped, sourceName = "") {
  const required = pricingReferenceRequiredColumns();
  const headerSet = new Set(headers.map((header) => String(header || "").trim()));
  const missing = required.filter((column) => !headerSet.has(column));
  const errors = [];
  const warnings = [];
  if (!items.length) errors.push("No valid pricing rows were found.");
  if (missing.length) errors.push(`Missing required columns: ${missing.join(", ")}.`);
  if (skipped) warnings.push(`${skipped} row${skipped === 1 ? "" : "s"} skipped during sanitizing.`);
  return {
    sourceName,
    items: sortPricingReferencePreviewItems(items),
    rowCount: items.length,
    headers,
    missing,
    skipped,
    layout: "normalized-pricing-reference",
    errors,
    warnings,
    canSave: !errors.length && items.length > 0,
  };
}

function sortPricingReferencePreviewItems(items = []) {
  const sectionOrders = new Map();
  let nextCategoryOrder = 1;
  const ordered = [...items].map((item, index) => {
    const sectionKey = safeId(normalizeCategoryTitle(item?.section || ""), `section-${index + 1}`);
    const explicitCategoryOrder = orderNumber(item?.category_order);
    if (!sectionOrders.has(sectionKey)) {
      const categoryOrder = explicitCategoryOrder || nextCategoryOrder;
      sectionOrders.set(sectionKey, categoryOrder);
      nextCategoryOrder = Math.max(nextCategoryOrder + 1, categoryOrder + 1);
    }
    return {
      item: {
        ...item,
        category_order: sectionOrders.get(sectionKey),
        item_order: orderNumber(item?.item_order) || index + 1,
      },
      index,
    };
  });
  ordered.sort((a, b) => {
    const categoryCompare = (orderNumber(a.item.category_order) || 999999) - (orderNumber(b.item.category_order) || 999999);
    if (categoryCompare) return categoryCompare;
    const itemOrderCompare = (orderNumber(a.item.item_order) || 999999) - (orderNumber(b.item.item_order) || 999999);
    if (itemOrderCompare) return itemOrderCompare;
    const sectionCompare = String(a.item?.section || "").toLowerCase().localeCompare(String(b.item?.section || "").toLowerCase());
    if (sectionCompare) return sectionCompare;
    const descriptionCompare = String(a.item?.description || "").toLowerCase().localeCompare(String(b.item?.description || "").toLowerCase());
    if (descriptionCompare) return descriptionCompare;
    return String(a.item?.id || "").toLowerCase().localeCompare(String(b.item?.id || "").toLowerCase()) || a.index - b.index;
  });
  return ordered.map(({ item }) => item);
}

function pricingReferenceRowStatus(row = {}) {
  const warnings = [];
  if (!String(row.section || "").trim()) warnings.push("section required");
  if (!String(row.description || "").trim()) warnings.push("description required");
  if (!String(row.unit_hint || "").trim()) warnings.push("unit_hint required");
  const cost = numberOrNull(row.internal_cost);
  const markup = numberOrNull(row.markup_multiplier);
  if (cost === null || cost <= 0) warnings.push("internal_cost must be positive");
  if (markup === null || markup <= 0) warnings.push("markup_multiplier must be positive");
  return warnings;
}

function pricingReferenceSaveBlockReason(result = state.pendingPricingReference) {
  if (state.pricingReferenceImportBusy) return "Import preview is still being prepared.";
  if (state.pricingReferenceSaveBusy) return "Pricing reference save is still running.";
  const mode = normalizePricingReferenceSettingsMode(state.pricingReferenceSettingsMode);
  if (!result) {
    return mode === PRICING_REFERENCE_SETTINGS_MODE_MANAGE
      ? "Select a pricing reference before saving changes."
      : "Upload a pricing catalog file before saving.";
  }
  if (mode === PRICING_REFERENCE_SETTINGS_MODE_MANAGE && !state.editingPricingReferenceId) {
    return "Select a pricing reference before saving changes.";
  }
  if (mode === PRICING_REFERENCE_SETTINGS_MODE_IMPORT && state.editingPricingReferenceId) {
    return "Return to Manage to save existing-reference edits, or upload a pricing catalog file to import.";
  }
  if (mode === PRICING_REFERENCE_SETTINGS_MODE_MANAGE && !pricingReferenceHasPendingChanges(result)) {
    return state.pricingReferenceSavedNotice ? "Saved." : "No changes to save.";
  }
  if (!String(elements.pricingReferenceName?.value || "").trim()) {
    return "Enter a pricing reference name before saving.";
  }
  if (elements.pricingReferenceTaxLabel && !String(elements.pricingReferenceTaxLabel.value || "").trim()) {
    return "Select a tax label before saving.";
  }
  if (elements.pricingReferenceTaxRate) {
    const taxRateText = String(elements.pricingReferenceTaxRate.value || "").trim();
    const taxRate = Number(taxRateText);
    if (!taxRateText || !Number.isFinite(taxRate) || taxRate < 0 || taxRate > 100) {
      return "Enter a tax rate between 0 and 100 before saving.";
    }
  }
  const nameConflict = pricingReferenceImportNameConflictMessage();
  if (nameConflict) return nameConflict;
  if (elements.pricingReferenceCurrency && !String(elements.pricingReferenceCurrency.value || "").trim()) {
    return "Choose a currency before saving.";
  }
  if (elements.pricingReferenceCurrency?.value === CUSTOM_CURRENCY_VALUE && !customCurrencyInputIsValid()) {
    return "Enter a 3-letter currency code before saving.";
  }
  const errors = Array.isArray(result.errors) ? result.errors.filter(Boolean) : [];
  if (errors.length) return errors.join(" ");
  if (!(result.items || []).length) return "Add at least one valid pricing row before saving.";
  if (!result.canSave) return "Fix the highlighted pricing-reference rows before saving.";
  return "";
}

function pricingReferenceSaveBlockReasonIsRequiredDetails(reason = "") {
  const text = String(reason || "").trim();
  return /(pricing reference name|tax label|tax rate|choose a currency|currency code|different pricing reference name)/i.test(text);
}

function setPricingReferenceModalBusyState(busy = false, reason = "") {
  const canManage = canManagePricingReferences();
  const disabled = busy || !canManage;
  const disabledTitle = !canManage ? pricingReferenceNoAccessReason() : busy ? reason || "Import preview is still being prepared." : "";
  const closeTitle = busy ? reason || "Import preview is still being prepared." : "";
  if (elements.pricingReferenceModal) {
    elements.pricingReferenceModal.classList.toggle("is-busy", busy);
    elements.pricingReferenceModal.classList.toggle("is-denied", !canManage);
  }
  [elements.pricingReferenceCloseButton, elements.pricingReferenceCancelButton].forEach((button) => {
    if (!button) return;
    button.disabled = busy;
    button.title = closeTitle;
    button.setAttribute("aria-disabled", String(busy));
  });
  if (elements.pricingReferenceFile) {
    elements.pricingReferenceFile.disabled = disabled;
  }
  if (elements.pricingReferenceTemplateButton) {
    elements.pricingReferenceTemplateButton.setAttribute("aria-disabled", String(disabled));
    elements.pricingReferenceTemplateButton.setAttribute("tabindex", disabled ? "-1" : "0");
    elements.pricingReferenceTemplateButton.title = disabledTitle;
  }
  if (typeof updatePricingReferenceExportButton === "function") updatePricingReferenceExportButton();
  const busyControls = [
    elements.pricingReferenceName,
    elements.pricingReferenceTaxLabel,
    elements.pricingReferenceTaxRate,
    elements.pricingReferenceCurrency,
    elements.pricingReferenceCurrencyCustom,
    elements.pricingReferenceManageTab,
    elements.pricingReferenceImportTab,
    elements.exportPricingReferenceButton,
    elements.deletePricingReferenceSelect,
    elements.deletePricingReferenceButton,
    elements.cancelPricingReferenceDeleteButton,
    elements.confirmPricingReferenceDeleteButton,
    elements.pricingReferenceTableCloseButton,
    ...Array.from(elements.pricingReferencePreview?.querySelectorAll("button, input, select, textarea") || []),
    ...Array.from(elements.pricingReferenceManageStatus?.querySelectorAll("button, input, select, textarea") || []),
    ...Array.from(elements.pricingReferenceTableOverlay?.querySelectorAll("button, input, select, textarea") || []),
  ].filter(Boolean);
  busyControls.forEach((control) => {
    if (busy) {
      if (!control.disabled) control.dataset.pricingReferenceBusyEnabled = "true";
      if (control.dataset.pricingReferenceBusyTitle === undefined) control.dataset.pricingReferenceBusyTitle = control.title || "";
      control.disabled = true;
      control.title = closeTitle || control.title || "";
      control.setAttribute("aria-disabled", "true");
      return;
    }
    if (control.dataset.pricingReferenceBusyEnabled === "true") {
      control.disabled = false;
      delete control.dataset.pricingReferenceBusyEnabled;
    }
    if (control.dataset.pricingReferenceBusyTitle !== undefined) {
      control.title = control.dataset.pricingReferenceBusyTitle;
      delete control.dataset.pricingReferenceBusyTitle;
    }
    control.setAttribute("aria-disabled", String(Boolean(control.disabled)));
  });
}

function setPricingReferenceSaveButtonState(options = {}) {
  const button = elements.pricingReferenceSaveButton;
  if (!button) return;
  const busy = Boolean(options.busy);
  const canManage = canManagePricingReferences();
  const mode = normalizePricingReferenceSettingsMode(state.pricingReferenceSettingsMode);
  const isEditingSave = mode === PRICING_REFERENCE_SETTINGS_MODE_MANAGE && Boolean(state.editingPricingReferenceId);
  const isImportSave = mode === PRICING_REFERENCE_SETTINGS_MODE_IMPORT && !state.editingPricingReferenceId;
  const hasChanges = pricingReferenceHasPendingChanges(state.pendingPricingReference);
  const workflowAllowsSave = (isImportSave && Boolean(state.pendingPricingReference)) || (isEditingSave && hasChanges);
  const blockReason = pricingReferenceSaveBlockReason(state.pendingPricingReference);
  const workflowReason = workflowAllowsSave ? "" : blockReason;
  const reason = canManage
    ? String((busy ? options.reason || blockReason : blockReason || workflowReason || options.reason) || "").trim()
    : pricingReferenceNoAccessReason();
  const canSave = canManage && workflowAllowsSave && Boolean(options.canSave) && !busy && !reason;
  const disabled = busy || !canManage || !canSave;
  button.disabled = disabled;
  button.textContent = busy
    ? String(options.busyLabel || "Saving...")
    : isEditingSave && state.pricingReferenceSavedNotice && !hasChanges
      ? "Saved"
      : isEditingSave
        ? "Save Changes"
        : "Save Reference";
  button.title = disabled ? reason : "";
  button.setAttribute("aria-disabled", String(disabled));
  syncPricingReferenceSettingsMode();
  setPricingReferenceModalBusyState(busy, reason);
}

function setPricingReferenceModalAccessState() {
  const canManage = canManagePricingReferences();
  if (elements.pricingReferenceNoAccess) elements.pricingReferenceNoAccess.hidden = canManage;
  if (elements.pricingReferenceEditorBody) elements.pricingReferenceEditorBody.hidden = !canManage;
  if (elements.pricingReferenceCancelButton) elements.pricingReferenceCancelButton.textContent = canManage ? "Cancel" : "Back";
  if (!canManage) {
    state.pendingPricingReference = null;
    state.pricingReferenceImportBusy = false;
    state.pricingReferenceSaveBusy = false;
    state.pricingReferenceImportToken = "";
  }
  setPricingReferenceSaveButtonState({
    canSave: false,
    reason: canManage ? pricingReferenceSaveBlockReason(state.pendingPricingReference) : pricingReferenceNoAccessReason(),
  });
  return canManage;
}

function blockPricingReferenceBusyInteraction(event) {
  if (!pricingReferenceOperationBusy()) return;
  const blockedControl = event.target.closest("button, a, label, input, select, textarea, [role='button'], [data-pricing-reference-close]");
  if (!blockedControl) return;
  event.preventDefault();
  event.stopPropagation();
}

function normalizePricingReferencePreviewItem(item = {}, index = 0) {
  const listFieldText = (value) => (
    Array.isArray(value)
      ? value.map(neutralizeFormulaText).filter(Boolean).join("; ")
      : neutralizeFormulaText(value || "")
  );
  return {
    id: safeId(item.id || `${item.section || "item"}-${item.description || index + 1}`, `item-${index + 1}`),
    section: normalizeCategoryTitle(neutralizeFormulaText(item.section || "")),
    description: cleanCustomerQuoteLineText(neutralizeFormulaText(item.description || "")),
    unit_hint: normalizeUnit(neutralizeFormulaText(item.unit_hint || item.unit || "")),
    internal_cost: item.internal_cost ?? "",
    markup_multiplier: item.markup_multiplier ?? "",
    category_order: orderNumber(item.category_order) ?? "",
    item_order: orderNumber(item.item_order) ?? index + 1,
    remarks: listFieldText(item.remarks),
    aliases: listFieldText(item.aliases),
    match_terms: listFieldText(item.match_terms),
    object_families: listFieldText(item.object_families),
    visual_references: Array.isArray(item.visual_references) ? item.visual_references : [],
    warning: item.warning || item.status || "",
  };
}

function refreshPricingReferencePreviewValidity(result = state.pendingPricingReference) {
  if (!result) return false;
  result.items = sortPricingReferencePreviewItems((result.items || []).map(normalizePricingReferencePreviewItem));
  if (result.items.length && Array.isArray(result.errors)) {
    result.errors = result.errors.filter((message) => !/(at least one|no editable pricing rows|no pricing rows)/i.test(String(message || "")));
  }
  const { duplicateIds, duplicateRowKeys } = pricingReferenceDuplicateMarkers(result.items);
  const invalid = result.items.flatMap((item) => {
    const warnings = pricingReferenceRowStatus(item);
    if (duplicateIds.has(item.id)) warnings.push("duplicate id");
    if (duplicateRowKeys.has(pricingReferenceDuplicateRowKey(item))) warnings.push("duplicate pricing row");
    item.warning = warnings.join("; ") || "OK";
    return warnings;
  });
  result.canSave = !invalid.length && result.items.length > 0 && !(result.errors || []).length;
  setPricingReferenceSaveButtonState({
    canSave: result.canSave,
    busy: pricingReferenceOperationBusy(),
    reason: pricingReferenceSaveBlockReason(result),
  });
  return result.canSave;
}

function blankPricingReferencePreviewItem(index = 0) {
  const existingItems = Array.isArray(state.pendingPricingReference?.items) ? state.pendingPricingReference.items : [];
  const previous = existingItems[existingItems.length - 1] || {};
  const nextOrder = Math.max(0, ...existingItems.map((item) => Number(item.item_order) || 0)) + 1;
  return normalizePricingReferencePreviewItem({
    id: `new-pricing-row-${Date.now().toString(36)}-${index + 1}`,
    section: previous.section || "",
    description: "",
    unit_hint: previous.unit_hint || "",
    internal_cost: "",
    markup_multiplier: previous.markup_multiplier || "",
    remarks: "",
    category_order: orderNumber(previous.category_order) ?? "",
    item_order: nextOrder,
  }, index);
}

function addPricingReferenceTableRow() {
  if (!state.pendingPricingReference || pricingReferenceOperationBusy()) return;
  pushPricingReferenceUndoSnapshot(pricingReferenceDraftUndoSnapshot(state.pendingPricingReference));
  state.pendingPricingReference.items = Array.isArray(state.pendingPricingReference.items) ? state.pendingPricingReference.items : [];
  state.pendingPricingReference.items.push(blankPricingReferencePreviewItem(state.pendingPricingReference.items.length));
  state.pricingReferenceSavedNotice = "";
  state.pricingReferenceEditNotice = "Added 1 editable pricing row.";
  refreshPricingReferencePreviewValidity(state.pendingPricingReference);
  renderPricingReferencePreview(state.pendingPricingReference);
  renderPricingReferenceTableOverlay(state.pendingPricingReference);
  renderPricingReferenceManageStatus(state.pendingPricingReference);
  window.setTimeout(() => {
    elements.pricingReferenceTableBody?.querySelector("tr:last-child [data-preview-field='description']")?.focus();
  }, 0);
}

function removePricingReferenceTableRow(rowIndex) {
  if (!state.pendingPricingReference || pricingReferenceOperationBusy()) return;
  if (!Number.isInteger(rowIndex) || rowIndex < 0 || rowIndex >= (state.pendingPricingReference.items || []).length) return;
  pushPricingReferenceUndoSnapshot(pricingReferenceDraftUndoSnapshot(state.pendingPricingReference));
  state.pendingPricingReference.items.splice(rowIndex, 1);
  state.pricingReferenceSavedNotice = "";
  state.pricingReferenceEditNotice = "Removed 1 pricing row.";
  refreshPricingReferencePreviewValidity(state.pendingPricingReference);
  renderPricingReferencePreview(state.pendingPricingReference);
  renderPricingReferenceTableOverlay(state.pendingPricingReference);
  renderPricingReferenceManageStatus(state.pendingPricingReference);
}

function pricingReferencePreviewTableRows(result = state.pendingPricingReference) {
  const items = Array.isArray(result?.items) ? result.items : [];
  return items.map((item, index) => `
    <tr data-preview-row="${index}">
      ${PRICING_REFERENCE_PREVIEW_FIELDS.map((field) => `
        <td><input class="pricing-preview-input" data-preview-field="${field}" data-preview-row="${index}" value="${escapeHtml(item[field] ?? "")}" placeholder="${escapeHtml(PRICING_REFERENCE_PREVIEW_PLACEHOLDERS[field] || "")}"></td>
      `).join("")}
      <td class="pricing-preview-status ${pricingReferenceStatusClass(item.warning || "OK")}">${escapeHtml(item.warning || "OK")}</td>
      <td class="pricing-reference-row-actions">
        <button class="secondary-button pricing-reference-row-remove" type="button" data-pricing-reference-remove-row="${index}" aria-label="Remove pricing row ${index + 1}">Remove</button>
      </td>
    </tr>
  `).join("");
}

function pricingReferenceStatusClass(status = "") {
  const text = String(status || "").trim().toLowerCase();
  if (!text || text === "ok") return "is-ok";
  if (/(required|must|duplicate|missing|invalid|error|failed|empty|positive|metadata)/.test(text)) return "is-error";
  return "is-warn";
}

function pricingReferenceSaveGuidance(result = state.pendingPricingReference) {
  if (!result) return "";
  const nameConflict = pricingReferenceImportNameConflictMessage();
  if (nameConflict) return "Choose a different pricing reference name before saving.";
  const reason = pricingReferenceSaveBlockReason(result);
  if (reason) {
    if (/^(Saved\.|No changes to save\.)/.test(reason)) return reason;
    if (pricingReferenceSaveBlockReasonIsRequiredDetails(reason)) {
      return `Complete the required pricing reference details before saving. ${reason}`;
    }
    return result.canSave ? reason : `Fix all flagged problems before saving this reference. ${reason}`;
  }
  return result.canSave
    ? "Basic row checks passed. Review any attention notes before saving."
    : "Fix all flagged problems before saving this reference.";
}

function pricingReferenceTableSummaryText(result = state.pendingPricingReference) {
  const items = Array.isArray(result?.items) ? result.items : [];
  return items.length
    ? `${items.length} editable pricing row${items.length === 1 ? "" : "s"}. ${pricingReferenceSaveGuidance(result)}`
    : "Upload a pricing catalog to review editable rows.";
}

function humanizeImportLayoutLabel(value = "") {
  const text = String(value || "normalized pricing reference")
    .trim()
    .replace(/^v\d+(?:\.\d+)?[-_\s]*/i, "")
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return "Normalized pricing reference";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function pricingReferenceRowIssues(items = []) {
  return items
    .map((item, index) => ({
      index: index + 1,
      status: String(item.warning || "").trim(),
    }))
    .filter((item) => item.status && item.status.toLowerCase() !== "ok");
}

function compactPreviewList(values = [], limit = 3) {
  const visible = values.slice(0, limit);
  const hidden = Math.max(0, values.length - visible.length);
  return `${visible.join("; ")}${hidden ? `; +${hidden} more` : ""}`;
}

function pricingReferencePreviewAttention(result = {}, context = {}) {
  const items = Array.isArray(result.items) ? result.items : [];
  const missing = Array.isArray(result.missing) ? result.missing.filter(Boolean) : [];
  const errors = Array.isArray(result.errors) ? result.errors.filter(Boolean) : [];
  const warnings = Array.isArray(result.warnings) ? result.warnings.filter(Boolean) : [];
  const skipped = Number(result.skipped || 0);
  const rowIssues = pricingReferenceRowIssues(items);
  const blockedReason = typeof pricingReferenceSaveBlockReason === "function" ? pricingReferenceSaveBlockReason(result) : "";
  const attention = [];
  errors
    .filter((message) => !(missing.length && /^missing required columns:/i.test(String(message))))
    .forEach((message) => attention.push({ tone: "error", label: "Import error", text: message }));
  if (missing.length) {
    attention.push({
      tone: "error",
      label: "Missing required columns",
      text: missing.join(", "),
    });
  }
  if (rowIssues.length) {
    attention.push({
      tone: "error",
      label: `${rowIssues.length} row${rowIssues.length === 1 ? "" : "s"} need edit`,
      text: `Open Review Rows. Examples: ${compactPreviewList(rowIssues.map((issue) => `row ${issue.index}: ${issue.status}`))}.`,
    });
  }
  if (!context.nameConflict && pricingReferenceSaveBlockReasonIsRequiredDetails(blockedReason)) {
    attention.push({
      tone: "warn",
      label: "Required details",
      text: blockedReason,
    });
  }
  if (result.canSave === false && items.length && !attention.some((item) => item.tone === "error")) {
    attention.push({
      tone: "error",
      label: "Rows need review",
      text: blockedReason || "Open Review Rows and fix the highlighted pricing-reference rows before saving.",
    });
  }
  warnings
    .filter((message) => !(skipped && /\bskipped\b/i.test(String(message))))
    .forEach((message) => attention.push({ tone: "warn", label: "Import warning", text: message }));
  if (context.currencyNeedsReview && !/currency/i.test(blockedReason)) {
    attention.push({
      tone: "warn",
      label: "Currency review",
      text: `"${context.currency || "Custom"}" is not in the standard currency selector. Confirm it is correct or choose another currency before saving.`,
    });
  }
  if (context.nameConflict) {
    attention.push({
      tone: "error",
      label: "Name already exists",
      text: context.nameConflict,
    });
  }
  if (skipped) {
    attention.push({
      tone: "warn",
      label: "Skipped rows",
      text: `${skipped} row${skipped === 1 ? "" : "s"} were skipped. Check the workbook if this is unexpected.`,
    });
  }
  if (!items.length && !errors.length) {
    attention.push({
      tone: "error",
      label: "No pricing rows",
      text: "No editable pricing rows were found in this upload.",
    });
  }
  return attention;
}

function pricingReferencePreviewNextStep(result = {}, attention = []) {
  const items = Array.isArray(result.items) ? result.items : [];
  const hasFixes = attention.some((item) => item.tone === "error");
  if (hasFixes) return "Fix the flagged import issues, then review the rows again.";
  if (attention.length) return "Review the attention notes, then save when the pricing reference is correct.";
  if (items.length) return `Review ${items.length} imported row${items.length === 1 ? "" : "s"} once before saving.`;
  return "Upload a pricing catalog to start the import check.";
}

function updatePricingReferenceGuidanceDisplays(result = state.pendingPricingReference) {
  if (elements.pricingReferenceTableSummary) {
    elements.pricingReferenceTableSummary.textContent = pricingReferenceTableSummaryText(result);
  }
  const guidance = elements.pricingReferencePreview?.querySelector(".pricing-reference-save-guidance");
  if (guidance) {
    const isBlocked = !result?.canSave || Boolean(pricingReferenceSaveBlockReason(result));
    guidance.textContent = sentenceLineBreakText(pricingReferenceSaveGuidance(result));
    guidance.classList.toggle("is-ok", !isBlocked);
    guidance.classList.toggle("is-blocked", isBlocked);
  }
}

function renderPricingReferenceTableOverlay(result = state.pendingPricingReference) {
  if (!elements.pricingReferenceTableBody) return;
  const items = Array.isArray(result?.items) ? result.items : [];
  updatePricingReferenceGuidanceDisplays(result);
  elements.pricingReferenceTableBody.innerHTML = items.length
    ? pricingReferencePreviewTableRows(result)
    : `<tr><td colspan="8" class="pricing-reference-table-empty">No editable rows yet. Use Add Row below to create the first pricing reference row.</td></tr>`;
  updatePricingReferenceUndoButton();
}

function openPricingReferenceTableOverlay() {
  if (!elements.pricingReferenceTableOverlay || pricingReferenceOperationBusy()) return;
  renderPricingReferenceTableOverlay(state.pendingPricingReference);
  state.pricingReferenceTableOpenedSnapshot = pricingReferenceDraftUndoSnapshot(state.pendingPricingReference);
  elements.pricingReferenceTableOverlay.hidden = false;
  elements.pricingReferenceTableOverlay.classList.add("is-open");
  window.setTimeout(() => elements.pricingReferenceTableCloseButton?.focus(), 0);
}

function closePricingReferenceTableOverlay() {
  if (!elements.pricingReferenceTableOverlay) return;
  const changedRows = pricingReferenceItemChangeCount(state.pricingReferenceTableOpenedSnapshot, state.pendingPricingReference);
  if (!elements.pricingReferenceTableOverlay.hidden && changedRows > 0) {
    state.pricingReferenceEditNotice = `${changedRows} pricing row${changedRows === 1 ? "" : "s"} amended in Review Rows.`;
    renderPricingReferenceManageStatus(state.pendingPricingReference);
  }
  state.pricingReferenceTableOpenedSnapshot = "";
  elements.pricingReferenceTableOverlay.classList.remove("is-open");
  elements.pricingReferenceTableOverlay.hidden = true;
}

function handlePricingReferencePreviewFocus(event) {
  const input = event.target.closest("[data-preview-field]");
  if (!input || !state.pendingPricingReference?.items) return;
  state.pricingReferenceEditPendingUndoSnapshot = pricingReferenceDraftUndoSnapshot(state.pendingPricingReference);
}

function handlePricingReferencePreviewInput(event) {
  const input = event.target.closest("[data-preview-field]");
  if (!input || !state.pendingPricingReference?.items) return;
  const rowIndex = Number(input.dataset.previewRow);
  const field = input.dataset.previewField;
  if (!Number.isInteger(rowIndex) || !state.pendingPricingReference.items[rowIndex]) return;
  if (state.pricingReferenceEditPendingUndoSnapshot) {
    pushPricingReferenceUndoSnapshot(state.pricingReferenceEditPendingUndoSnapshot);
    state.pricingReferenceEditPendingUndoSnapshot = "";
  }
  state.pendingPricingReference.items[rowIndex][field] = input.value;
  if (PRICING_REFERENCE_METADATA_STALE_FIELDS.has(field)) {
    state.pendingPricingReference.items[rowIndex].match_terms = "";
    state.pendingPricingReference.items[rowIndex].object_families = "";
  }
  state.pricingReferenceSavedNotice = "";
  refreshPricingReferencePreviewValidity(state.pendingPricingReference);
  const row = input.closest("tr");
  const status = row?.querySelector(".pricing-preview-status");
  if (status) {
    const statusText = state.pendingPricingReference.items[rowIndex].warning || "OK";
    status.textContent = statusText;
    status.className = `pricing-preview-status ${pricingReferenceStatusClass(statusText)}`;
  }
  updatePricingReferenceGuidanceDisplays(state.pendingPricingReference);
  renderPricingReferenceManageStatus(state.pendingPricingReference);
}

function renderPricingReferencePreview(result = null, options = {}) {
  if (!elements.pricingReferencePreview) return;
  if (!result) {
    elements.pricingReferencePreview.innerHTML = "";
    renderPricingReferenceTableOverlay(null);
    setPricingReferenceSaveButtonState({ canSave: false, reason: pricingReferenceSaveBlockReason(null) });
    renderPricingReferenceManageStatus(null);
    syncPricingReferenceImportSetupVisibility();
    return;
  }
  if (String(result.layout || "") === "importing") {
    state.pendingPricingReference = null;
    elements.pricingReferencePreview.className = "pricing-reference-preview importing";
    elements.pricingReferencePreview.innerHTML = `
      <div class="pricing-reference-import-overlay" role="status" aria-live="polite">
        <span class="pricing-reference-spinner" aria-hidden="true"></span>
        <strong>Preparing import preview</strong>
        <p>Please wait while AI reads the file and builds editable pricing rows.</p>
        <span class="ai-elapsed pricing-reference-import-elapsed" id="pricingReferenceImportElapsed" aria-live="polite">Elapsed 0:00</span>
      </div>
    `;
    setPricingReferenceSaveButtonState({
      canSave: false,
      busy: true,
      busyLabel: "Importing...",
      reason: "Import preview is still being prepared.",
    });
    renderPricingReferenceManageStatus(null);
    syncPricingReferenceImportSetupVisibility();
    return;
  }
  if (state.pricingReferenceSaveBusy) {
    elements.pricingReferencePreview.className = "pricing-reference-preview saving";
    elements.pricingReferencePreview.innerHTML = pricingReferenceSaveProgressMarkup();
    syncPricingReferenceImportSetupVisibility();
    return;
  }
  result.items = sortPricingReferencePreviewItems((result.items || []).map(normalizePricingReferencePreviewItem));
  if (options.syncCurrencyControls && result.currency) setPricingReferenceCurrencyControls(result.currency);
  const selectedCurrencyMode = elements.pricingReferenceCurrency?.value;
  const modalCurrency = pricingReferenceModalCurrency();
  const currency = selectedCurrencyMode === CUSTOM_CURRENCY_VALUE
    ? modalCurrency || "Custom"
    : normalizeCurrencyLabel(result.currency || modalCurrency || DEFAULT_CURRENCY_LABEL);
  refreshPricingReferencePreviewValidity(result);
  const warnings = result.warnings || [];
  const rowsCanSave = Boolean(result.canSave);
  const saveBlockReason = pricingReferenceSaveBlockReason(result);
  const requiredDetailsBlocked = pricingReferenceSaveBlockReasonIsRequiredDetails(saveBlockReason);
  const informationalBlockReason = /^(Saved\.|No changes to save\.)/.test(saveBlockReason);
  const hardSaveBlockReason = Boolean(saveBlockReason) && !requiredDetailsBlocked && !informationalBlockReason;
  const canSave = rowsCanSave && !saveBlockReason;
  const currencyNeedsReview = Boolean(currency) && !isValidCurrencyCode(currency);
  const nameConflict = pricingReferenceImportNameConflictMessage();
  const attention = pricingReferencePreviewAttention(result, { currency, currencyNeedsReview, nameConflict });
  const hasFixes = attention.some((item) => item.tone === "error") || !rowsCanSave || hardSaveBlockReason;
  const hasReviewNotes = attention.length > 0 || requiredDetailsBlocked;
  const tone = hasFixes ? "error" : hasReviewNotes || warnings.length ? "warn" : "ok";
  elements.pricingReferencePreview.className = `pricing-reference-preview ${tone}`;
  elements.pricingReferencePreview.innerHTML = `
    <div class="pricing-reference-attention ${attention.length ? "" : "is-clear"}">
      <strong>${attention.length ? "Needs attention" : "No missing required info detected"}</strong>
      ${attention.length ? `
        <ul>
          ${attention.map((item) => `
            <li class="${item.tone === "error" ? "is-error" : "is-warn"}">
              <span>${escapeHtml(item.label)}</span>
              <p>${escapeHtml(item.text)}</p>
            </li>
          `).join("")}
        </ul>
      ` : `<p>Required columns are present and imported rows pass the basic checks.</p>`}
    </div>
    <p class="pricing-reference-save-guidance ${canSave ? "is-ok" : "is-blocked"}">${escapeHtml(sentenceLineBreakText(pricingReferenceSaveGuidance(result)))}</p>
    <div class="pricing-reference-preview-actions">
      <button class="secondary-button pricing-reference-table-open" type="button" data-pricing-reference-table-open>${result.items.length ? `Review ${result.items.length} Row${result.items.length === 1 ? "" : "s"}` : "Review Rows"}</button>
    </div>
  `;
  renderPricingReferenceTableOverlay(result);
  setPricingReferenceSaveButtonState({
    canSave: rowsCanSave,
    busy: pricingReferenceOperationBusy(),
    reason: saveBlockReason,
  });
  renderPricingReferenceManageStatus(result);
  syncPricingReferenceImportSetupVisibility();
  if (options.scrollIntoView) {
    window.requestAnimationFrame(() => {
      elements.pricingReferencePreview?.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
  }
}


function pricingReferenceModalTax() {
  return {
    label: normalizeTaxLabel(elements.pricingReferenceTaxLabel?.value),
    rate: taxRateFromPercentInput(elements.pricingReferenceTaxRate?.value, DEFAULT_TAX_RATE),
  };
}

function pricingReferenceModalCurrency() {
  const selected = elements.pricingReferenceCurrency?.value;
  if (selected === CUSTOM_CURRENCY_VALUE) return customCurrencyInputIsValid() ? normalizedCustomCurrencyInput() : "";
  return supportedCurrencyLabel(selected);
}

function resetPricingReferenceTaxInputs() {
  setPricingReferenceTaxControls({ label: DEFAULT_TAX_LABEL, rate: DEFAULT_TAX_RATE });
  setPricingReferenceCurrencyControls(DEFAULT_CURRENCY_LABEL);
}

function applyPricingReferenceImportMetadata(result = {}) {
  if (!result || typeof result !== "object") return;
  const suggestedLabel = String(result.suggested_label || result.label || "").trim();
  if (suggestedLabel && elements.pricingReferenceName && !String(elements.pricingReferenceName.value || "").trim()) {
    elements.pricingReferenceName.value = neutralizeFormulaText(suggestedLabel);
  }
  if (result.tax && typeof result.tax === "object") {
    setPricingReferenceTaxControls(result.tax);
  }
  if (result.currency) {
    setPricingReferenceCurrencyControls(result.currency);
  }
}

async function validatePricingReferenceFile(file) {
  if (!file) return pricingReferenceValidationResult([], [], 0, "");
  if (file.size > MAX_PRICING_REFERENCE_FILE_BYTES) {
    return { ...pricingReferenceValidationResult([], [], 0, file.name), errors: ["Pricing reference file is larger than 10 MB."] };
  }
  const extension = file.name.split(".").pop().toLowerCase();
  if (!["csv", "xlsx", "md"].includes(extension)) {
    return { ...pricingReferenceValidationResult([], [], 0, file.name), errors: ["Upload a .xlsx, .csv, or .md pricing reference file."] };
  }
  const { ok, data } = await postJson("/api/settings/pricing-references/import-preview", {
    filename: file.name,
    data_url: await fileToDataUrl(file),
    currency: pricingReferenceModalCurrency(),
    tax: pricingReferenceModalTax(),
  });
  if (ok) return data;
  return {
    ...pricingReferenceValidationResult([], [], 0, file.name),
    errors: genericFailureMessages(data),
    error_reference: errorReferenceFrom(data),
  };
}

async function handlePricingReferenceFileChange() {
  setPricingReferenceSettingsMode(PRICING_REFERENCE_SETTINGS_MODE_IMPORT);
  const file = elements.pricingReferenceFile.files?.[0];
  if (!file) {
    return;
  }
  const previousEditingReferenceId = state.editingPricingReferenceId;
  const currentNameId = pricingReferenceNameId(elements.pricingReferenceName?.value || "");
  state.pricingReferenceImportFileSelected = true;
  state.pricingReferenceSaveBusy = false;
  stopElapsedTimer("pricingReferenceSaveElapsed");
  state.pricingReferenceEditSnapshot = "";
  state.pricingReferenceSavedNotice = "";
  if (elements.pricingReferenceFileName) {
    elements.pricingReferenceFileName.textContent = file.name || "Selected file";
  }
  if (previousEditingReferenceId && currentNameId === previousEditingReferenceId && elements.pricingReferenceName) {
    elements.pricingReferenceName.value = "";
  }
  state.editingPricingReferenceId = "";
  const importToken = `${Date.now()}-${file.name}-${file.size}`;
  state.pricingReferenceImportToken = importToken;
  state.pricingReferenceImportBusy = true;
  setPricingReferenceSaveButtonState({ busy: true, busyLabel: "Importing...", reason: pricingReferenceSaveBlockReason(null) });
  renderPricingReferencePreview({
    ...pricingReferenceValidationResult([], [], 0, file.name),
    layout: "importing",
    warnings: ["Import preview is still being prepared."],
    errors: [],
  });
  const importStartedAt = Date.now();
  startElapsedTimer("pricingReferenceImportElapsed", importStartedAt);
  let result;
  try {
    result = await validatePricingReferenceFile(file);
  } catch (error) {
    result = {
      ...pricingReferenceValidationResult([], [], 0, file.name),
      errors: genericFailureMessages(),
    };
  }
  if (state.pricingReferenceImportToken !== importToken) return;
  state.pendingPricingReference = result;
  state.pricingReferenceImportBusy = false;
  stopElapsedTimer("pricingReferenceImportElapsed");
  applyPricingReferenceImportMetadata(result);
  renderPricingReferencePreview(result, { syncCurrencyControls: true, scrollIntoView: true });
}

async function downloadPricingReferenceTemplate(event) {
  event.preventDefault();
  if (pricingReferenceOperationBusy() || !canManagePricingReferences()) {
    return;
  }
  const templateUrl = `/api/pricing-reference/template.xlsx?template=examples-v3&t=${Date.now()}`;
  try {
    const link = document.createElement("a");
    link.href = templateUrl;
    link.download = "pricing-reference-template.xlsx";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (error) {
    window.location.href = templateUrl;
  }
}

function exportSelectedPricingReference(event) {
  event.preventDefault();
  if (pricingReferenceOperationBusy()) return;
  const reference = deletionPricingReference();
  const reason = pricingReferenceExportBlockReason(reference);
  if (reason) {
    updatePricingReferenceExportButton();
    return;
  }
  const referenceId = String(reference.id || "").trim();
  const query = new URLSearchParams({
    source: String(reference.source || "local"),
    t: String(Date.now()),
  });
  const exportUrl = `/api/settings/pricing-references/${encodeURIComponent(referenceId)}/export.xlsx?${query.toString()}`;
  try {
    const link = document.createElement("a");
    link.href = exportUrl;
    link.download = `${safeId(reference.label || reference.id || "pricing-reference", "pricing-reference")}.xlsx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (error) {
    window.location.href = exportUrl;
  }
}

function openPricingReferenceModal(options = {}) {
  stopElapsedTimer("pricingReferenceImportElapsed");
  stopElapsedTimer("pricingReferenceSaveElapsed");
  state.pendingPricingReference = null;
  state.editingPricingReferenceId = "";
  if (!options.preserveMode) state.pricingReferenceSettingsMode = PRICING_REFERENCE_SETTINGS_MODE_MANAGE;
  state.pricingReferenceImportFileSelected = false;
  state.pricingReferenceImportBusy = false;
  state.pricingReferenceSaveBusy = false;
  state.pricingReferenceImportToken = "";
  state.pricingReferenceEditSnapshot = "";
  state.pricingReferenceEditUndoStack = [];
  state.pricingReferenceEditPendingUndoSnapshot = "";
  state.pricingReferenceTableOpenedSnapshot = "";
  state.pricingReferenceEditNotice = "";
  state.pricingReferenceAutoLoadToken = "";
  state.pricingReferenceSavedNotice = "";
  hidePricingReferenceDeleteConfirm();
  if (elements.pricingReferenceName) elements.pricingReferenceName.value = "";
  if (elements.pricingReferenceFile) elements.pricingReferenceFile.value = "";
  if (elements.pricingReferenceFileName) elements.pricingReferenceFileName.textContent = "No file chosen";
  resetPricingReferenceTaxInputs();
  renderPricingReferencePreview(null);
  renderPricingReferenceDeleteOptions();
  syncPricingReferenceSettingsMode();
  elements.pricingReferenceModal.hidden = false;
  elements.pricingReferenceModal.classList.add("is-open");
  state.restorableOverlay = "pricing_reference_settings";
  saveSessionState();
  const canManage = setPricingReferenceModalAccessState();
  if (canManage && state.pricingReferenceSettingsMode === PRICING_REFERENCE_SETTINGS_MODE_MANAGE) editSelectedPricingReference({ openTable: false });
  const focusTarget = state.pricingReferenceSettingsMode === PRICING_REFERENCE_SETTINGS_MODE_IMPORT
    ? elements.pricingReferenceFile
    : canManage ? elements.deletePricingReferenceSelect : elements.pricingReferenceCancelButton;
  window.setTimeout(() => focusTarget?.focus(), 0);
}

function closePricingReferenceModal() {
  if (pricingReferenceOperationBusy()) {
    return;
  }
  closePricingReferenceTableOverlay();
  stopElapsedTimer("pricingReferenceImportElapsed");
  stopElapsedTimer("pricingReferenceSaveElapsed");
  elements.pricingReferenceModal.classList.remove("is-open");
  elements.pricingReferenceModal.hidden = true;
  state.pendingPricingReference = null;
  state.editingPricingReferenceId = "";
  state.editingPricingReferenceSource = "";
  state.pricingReferenceSettingsMode = PRICING_REFERENCE_SETTINGS_MODE_MANAGE;
  state.pricingReferenceImportFileSelected = false;
  state.pricingReferenceImportBusy = false;
  state.pricingReferenceSaveBusy = false;
  state.pricingReferenceImportToken = "";
  state.pricingReferenceEditSnapshot = "";
  state.pricingReferenceEditUndoStack = [];
  state.pricingReferenceEditPendingUndoSnapshot = "";
  state.pricingReferenceTableOpenedSnapshot = "";
  state.pricingReferenceEditNotice = "";
  state.pricingReferenceAutoLoadToken = "";
  state.pricingReferenceSavedNotice = "";
  hidePricingReferenceDeleteConfirm();
  state.restorableOverlay = "";
  saveSessionState();
  syncPricingReferenceSettingsMode();
}

function restoreRestorableOverlay() {
  if (state.restorableOverlay === "pricing_reference_settings") {
    openPricingReferenceModal({ preserveMode: true });
    return;
  }
  if (state.restorableOverlay === "basis_chat") {
    if (elements.basisChatOverlay?.hidden) restoreBasisChatOverlay();
    return;
  }
  if (state.restorableOverlay === "analysis_confirm") {
    requestStartAnalysis(state.pendingAnalysisMode);
    return;
  }
  if (state.restorableOverlay === "excel_ready") showGeneratedExportReadyModal(false);
  if (state.restorableOverlay === "pdf_ready") showGeneratedExportReadyModal(true);
}

async function editSelectedPricingReference(options = {}) {
  const summaryReference = deletionPricingReference();
  const reason = pricingReferenceEditBlockReason(summaryReference);
  if (reason) return;
  const loadToken = `${summaryReference.id || ""}-${Date.now()}`;
  state.pricingReferenceAutoLoadToken = loadToken;
  state.editingPricingReferenceId = summaryReference.id || "";
  state.editingPricingReferenceSource = summaryReference.source || "";
  state.pendingPricingReference = {
    sourceName: summaryReference.label || summaryReference.id || "Pricing reference",
    items: [],
    rowCount: 0,
    errors: [],
    warnings: [],
    canSave: false,
    layout: "loading-pricing-reference",
  };
  state.pricingReferenceImportFileSelected = false;
  state.pricingReferenceImportBusy = false;
  state.pricingReferenceSaveBusy = false;
  state.pricingReferenceImportToken = "";
  stopElapsedTimer("pricingReferenceImportElapsed");
  stopElapsedTimer("pricingReferenceSaveElapsed");
  state.pricingReferenceEditNotice = "";
  renderPricingReferenceManageStatus(state.pendingPricingReference);
  const reference = await fetchPricingReferenceDetail(summaryReference).catch(() => null);
  if (state.pricingReferenceAutoLoadToken !== loadToken) return;
  if (!reference || !Array.isArray(reference.items) || !reference.items.length) {
    state.editingPricingReferenceId = "";
    state.pendingPricingReference = {
      ...pricingReferenceValidationResult([], [], 0, summaryReference?.label || "Pricing reference"),
      errors: ["Could not load editable pricing reference rows."],
    };
    renderPricingReferencePreview({
      ...state.pendingPricingReference,
    }, { scrollIntoView: true });
    syncControlStates();
    return;
  }
  const existingIndex = state.pricingReferences.findIndex((item) => (
    item.id === reference.id && pricingReferenceSelectValue(item) === pricingReferenceSelectValue(summaryReference)
  ));
  if (existingIndex >= 0) state.pricingReferences[existingIndex] = reference;
  state.editingPricingReferenceId = reference.id || "";
  state.pendingPricingReference = pricingReferencePreviewFromReference(reference);
  if (elements.pricingReferenceName) elements.pricingReferenceName.value = reference.label || reference.id || "";
  if (elements.pricingReferenceFile) elements.pricingReferenceFile.value = "";
  if (elements.pricingReferenceFileName) elements.pricingReferenceFileName.textContent = "Editing existing reference";
  setPricingReferenceTaxControls(state.pendingPricingReference.tax);
  setPricingReferenceCurrencyControls(state.pendingPricingReference.currency);
  setPricingReferenceSettingsMode(PRICING_REFERENCE_SETTINGS_MODE_MANAGE);
  renderPricingReferencePreview(state.pendingPricingReference);
  capturePricingReferenceEditSnapshot(state.pendingPricingReference);
  setPricingReferenceSaveButtonState({
    canSave: Boolean(state.pendingPricingReference?.canSave),
    reason: pricingReferenceSaveBlockReason(state.pendingPricingReference),
  });
  renderPricingReferenceManageStatus(state.pendingPricingReference);
  if (options.openTable) openPricingReferenceTableOverlay();
  syncControlStates();
}

async function savePricingReferenceFromModal(event) {
  event.preventDefault();
  if (!canManagePricingReferences()) {
    setPricingReferenceSaveButtonState({
      canSave: false,
      reason: pricingReferenceNoAccessReason(),
    });
    return;
  }
  if (pricingReferenceOperationBusy()) {
    setPricingReferenceSaveButtonState({
      busy: true,
      busyLabel: state.pricingReferenceImportBusy ? "Importing..." : "Saving...",
      reason: pricingReferenceSaveBlockReason(state.pendingPricingReference),
    });
    return;
  }
  const name = elements.pricingReferenceName.value.trim();
  const tax = pricingReferenceModalTax();
  const currency = pricingReferenceModalCurrency();
  const result = state.pendingPricingReference;
  const canSave = result?.canSave ?? Boolean(result && !result.errors.length && result.items.length);
  const saveBlockReason = pricingReferenceSaveBlockReason(result);
  if (!name || !result || !canSave || saveBlockReason) {
    if (state.editingPricingReferenceId && result && !pricingReferenceHasPendingChanges(result)) {
      state.pricingReferenceSavedNotice = "No changes to save; this pricing reference already matches the saved version.";
      renderPricingReferenceManageStatus(result);
      setPricingReferenceSaveButtonState({
        canSave: false,
        reason: pricingReferenceSaveBlockReason(result),
      });
      return;
    }
    renderPricingReferencePreview(result || pricingReferenceValidationResult([], [], 0, ""));
    return;
  }
  const saveStartedAt = Date.now();
  state.pricingReferenceSaveBusy = true;
  setPricingReferenceSaveButtonState({
    busy: true,
    busyLabel: "Saving...",
    reason: "Finalizing pricing reference matching metadata before saving.",
  });
  renderPricingReferencePreview(result);
  renderPricingReferenceManageStatus(result);
  startElapsedTimer("pricingReferenceSaveElapsed", saveStartedAt);
  let didSave = false;
  try {
    const { ok, data } = await postJson("/api/settings/pricing-references", {
      id: state.editingPricingReferenceId || safeId(name, `pricing-ref-${Date.now().toString(36)}`),
      source: state.editingPricingReferenceSource || "",
      label: name,
      description: result.description || `Imported from ${result.sourceName || "settings upload"}`,
      tax,
      currency,
      items: result.items,
      update_existing: Boolean(state.editingPricingReferenceId),
      editing_reference_id: state.editingPricingReferenceId || "",
    });
    if (!ok) {
      state.pricingReferenceSaveBusy = false;
      stopElapsedTimer("pricingReferenceSaveElapsed");
      renderPricingReferencePreview({ ...result, errors: genericFailureMessages(data), error_reference: errorReferenceFrom(data) });
      renderPricingReferenceManageStatus({ ...result, errors: genericFailureMessages(data), error_reference: errorReferenceFrom(data) });
      setPricingReferenceSaveButtonState({
        canSave: Boolean(result?.canSave),
        reason: pricingReferenceSaveBlockReason(result),
      });
      syncControlStates();
      return;
    }
    didSave = true;
    const savedReference = data.pricing_reference || {};
    await loadProfiles();
    if (savedReference.id) {
      state.pricingReferenceId = savedReference.id || "";
      state.pricingReferenceSource = pricingReferenceSelectionFromValue(pricingReferenceSelectValue(savedReference)).source;
      persistLastPricingReferenceSelection(savedReference);
    }
    syncSelectedPricingReference();
    renderProfileOptions();
    renderPricingReferenceDeleteOptions();
    hidePricingReferenceDeleteConfirm();
    if (elements.deletePricingReferenceSelect && savedReference.id) {
      const savedValue = pricingReferenceSelectValue(savedReference);
      if ([...elements.deletePricingReferenceSelect.options].some((option) => option.value === savedValue)) {
        elements.deletePricingReferenceSelect.value = savedValue;
      }
    }
    state.editingPricingReferenceId = savedReference.id || state.editingPricingReferenceId || safeId(name, "pricing-reference");
    state.editingPricingReferenceSource = savedReference.source || state.editingPricingReferenceSource || "local";
    state.pricingReferenceSettingsMode = PRICING_REFERENCE_SETTINGS_MODE_MANAGE;
    state.pricingReferenceImportFileSelected = false;
    state.pendingPricingReference = {
      ...result,
      sourceName: savedReference.label || name,
      referenceId: savedReference.id || state.editingPricingReferenceId,
      layout: "saved-pricing-reference",
      errors: [],
      warnings: [],
      canSave: true,
    };
    if (elements.pricingReferenceName) elements.pricingReferenceName.value = savedReference.label || name;
    if (elements.pricingReferenceFile) elements.pricingReferenceFile.value = "";
    if (elements.pricingReferenceFileName) elements.pricingReferenceFileName.textContent = data.unchanged ? "Already saved" : "Saved reference";
    setPricingReferenceTaxControls(tax);
    setPricingReferenceCurrencyControls(currency);
    state.pricingReferenceSaveBusy = false;
    stopElapsedTimer("pricingReferenceSaveElapsed");
    updatePricingReferenceDeleteButton();
    renderPricingReferencePreview(state.pendingPricingReference);
    capturePricingReferenceEditSnapshot(state.pendingPricingReference);
    const metadataEnrichmentStatus = String(data.metadata_enrichment_status || "").trim();
    state.pricingReferenceSavedNotice = data.unchanged
      ? "No file changes were needed."
      : metadataEnrichmentStatus === "completed"
        ? "Saved successfully. Matching clues updated."
        : metadataEnrichmentStatus === "failed"
          ? "Saved, but matching clue enrichment did not complete."
          : "Saved successfully.";
    setPricingReferenceSaveButtonState({
      canSave: Boolean(state.pendingPricingReference?.canSave),
      reason: pricingReferenceSaveBlockReason(state.pendingPricingReference),
    });
    renderPricingReferenceManageStatus(state.pendingPricingReference);
    syncControlStates();
  } catch (error) {
    state.pricingReferenceSaveBusy = false;
    stopElapsedTimer("pricingReferenceSaveElapsed");
    const fallbackResult = state.pendingPricingReference || result || pricingReferenceValidationResult([], [], 0, "");
    const errors = didSave
      ? ["Saved, but the settings list could not refresh. Reload the app and check the pricing reference list."]
      : genericFailureMessages(error);
    const failureResult = {
      ...fallbackResult,
      errors,
      error_reference: errorReferenceFrom(error),
      canSave: Boolean(fallbackResult?.canSave),
    };
    renderPricingReferencePreview(failureResult);
    renderPricingReferenceManageStatus(failureResult);
    setPricingReferenceSaveButtonState({
      canSave: Boolean(fallbackResult?.canSave),
      reason: pricingReferenceSaveBlockReason(fallbackResult),
    });
    syncControlStates();
  }
}

async function deleteRepoPricingReference(referenceId, source = "local") {
  const reference = state.pricingReferences.find((item) => item.id === referenceId && String(item.source || "bundled") === source);
  if (!reference) return;
  const reason = protectedPricingReferenceReason(reference);
  if (reason) {
    updatePricingReferenceDeleteButton();
    return;
  }
  state.pricingReferenceDeleteBusy = true;
  state.pricingReferenceDeleteError = "";
  renderPricingReferenceDeleteConfirm();
  setPricingReferenceModalBusyState(true, "Deleting pricing reference...");
  updatePricingReferenceDeleteButton();
  try {
    const query = source ? `?source=${encodeURIComponent(source)}` : "";
    const { ok, data } = await fetch(`/api/settings/pricing-references/${encodeURIComponent(reference.id)}${query}`, {
      method: "DELETE",
      headers: state.csrfToken ? { [state.csrfHeaderName]: state.csrfToken } : {},
    }).then(async (response) => ({ ok: response.ok, data: await response.json().catch(() => ({})) }));
    if (!ok) {
      state.pricingReferenceDeleteError = genericFailureMessages(data).join(" ");
      renderPricingReferenceDeleteConfirm();
      return;
    }
    state.pricingReferences = mergePricingReferences(Array.isArray(data.pricing_references) ? data.pricing_references : state.pricingReferences);
    if (state.pricingReferenceId === reference.id) {
      const fallback = defaultPricingReference() || state.pricingReferences[0] || null;
      state.pricingReferenceId = fallback?.id || "";
      state.pricingReferenceSource = fallback ? pricingReferenceSelectionFromValue(pricingReferenceSelectValue(fallback)).source : "";
    }
    hidePricingReferenceDeleteConfirm();
    clearPricingReferenceDraft({ clearFile: true, resetMetadata: true });
    state.pricingReferenceSettingsMode = PRICING_REFERENCE_SETTINGS_MODE_MANAGE;
    await loadProfiles();
    syncSelectedPricingReference();
    renderProfileOptions();
    renderPricingReferenceDeleteOptions();
    syncPricingReferenceSettingsMode();
  } catch (error) {
    state.pricingReferenceDeleteError = genericFailureMessages(error).join(" ");
    renderPricingReferenceDeleteConfirm();
  } finally {
    state.pricingReferenceDeleteBusy = false;
    setPricingReferenceModalBusyState(false);
    renderPricingReferenceDeleteConfirm();
    updatePricingReferenceDeleteButton();
    syncControlStates();
  }
}

function requestSelectedPricingReferenceDelete() {
  const selected = deletionPricingReference();
  if (!selected || !canDeleteSelectedPricingReference()) {
    updatePricingReferenceDeleteButton();
    return;
  }
  showPricingReferenceDeleteConfirm(selected);
}

async function deleteSelectedPricingReference() {
  const selected = pricingReferenceDeleteConfirmReference();
  if (!selected || protectedPricingReferenceReason(selected)) {
    hidePricingReferenceDeleteConfirm();
    updatePricingReferenceDeleteButton();
    return;
  }
  await deleteRepoPricingReference(selected.id, selected.source || "local");
}

async function loadProfiles() {
  const { ok, data } = await getJson("/api/profiles");
  if (ok && Array.isArray(data.profiles)) {
    state.profiles = data.profiles;
    state.workspace = data.workspace && typeof data.workspace === "object" ? data.workspace : null;
    state.defaultProfileId = data.default_profile_id || DEFAULT_PROFILE_ID;
    state.defaultPricingReferenceId = data.default_pricing_reference_id || DEFAULT_PRICING_REFERENCE_ID;
    state.pricingReferences = mergePricingReferences(Array.isArray(data.pricing_references) ? data.pricing_references : []);
    if (state.pricingReferenceId) {
      syncSelectedPricingReference();
    }
  }
  await loadCompanyProfiles();
  renderProfileOptions();
}

async function loadCompanyProfiles() {
  if (!canManageProfiles()) {
    state.companyProfiles = [];
    return;
  }
  const { ok, data } = await getJson("/api/settings/profiles", { logFetchFailure: false });
  if (!ok) {
    state.companyProfiles = [];
    return;
  }
  state.companyProfiles = (Array.isArray(data.company_profiles) ? data.company_profiles : [])
    .map(normalizeCompanyProfile)
    .sort((left, right) => String(left.label || left.id || "").localeCompare(String(right.label || right.id || ""), undefined, { sensitivity: "base" }));
  renderPresetOptions();
}

function clearGeneratedQuoteState() {
  state.quoteBasis = { ...EMPTY_BASIS };
  state.quoteBasisSections = [];
  state.lineItems = [];
  state.outputRows = [];
  state.originalOutputRows = [];
  state.outputErrors = [];
  state.outputRevision = 0;
  state.analysisFindings = [];
  state.blockingClarificationQuestions = [];
  state.basisConfirmed = false;
  state.aiFailed = false;
  state.draftSource = "";
  state.lastAnalysisMode = ANALYSIS_MODE_STANDARD;
  state.activeJob = null;
  state.originalAnalysisSnapshot = null;
  renderBasisEmptyState();
  clearAiFailureBanner();
  renderMessages([]);
  setDownloadFiles([]);
  renderMatchSummary({});
  renderPricingMatches([]);
  clearPricingReviewMessages();
  setResultStatus("No job yet");
}

function pendingPricingReferenceSelection() {
  return pricingReferenceSelectionFromValue(elements.profileSelect?.value || "");
}

function applyPendingPricingReferenceSelection() {
  const nextSelection = pendingPricingReferenceSelection();
  if (!nextSelection.pricingReferenceId) return false;
  if (nextSelection.pricingReferenceId === state.pricingReferenceId && nextSelection.source === state.pricingReferenceSource) {
    return false;
  }
  state.pricingReferenceId = nextSelection.pricingReferenceId;
  state.pricingReferenceSource = nextSelection.source;
  syncSelectedPricingReference();
  persistLastPricingReferenceSelection();
  renderProfileOptions();
  clearGeneratedQuoteState();
  setWorkflowStage(state.images.length ? (canStartAnalysis() ? "ready_to_analyze" : "details_review") : "needs_images");
  syncControlStates();
  return true;
}

function handleProfileSelectionChange() {
  updateSidePanelNav();
}

function buildPayload(options = {}) {
  syncRichTextSources();
  const generator = currentGenerator();
  const pricingReference = currentPricingReference();
  const profileId = generationProfileIdForPayload();
  const includeBoothDimensions = options.includeBoothDimensions !== false;
  const includeDraftContext = options.includeDraftContext !== false;
  const project = {
    title: elements.projectTitle.value.trim(),
    show_name: elements.showName?.value.trim() || "",
  };
  if (includeBoothDimensions) {
    project.booth_width = state.boothDimensions.booth_width;
    project.booth_depth = state.boothDimensions.booth_depth;
    project.booth_size = state.boothDimensions.booth_size;
    project.dimension_source = state.boothDimensions.dimension_source;
  }
  return {
    profile_id: profileId,
    pricing_reference_id: pricingReference?.id || state.pricingReferenceId || "",
    pricing_reference: pricingReference ? {
      ...pricingReference,
      source: pricingReference.source || "bundled",
      tax: pricingReference.tax || selectedPricingReferenceTax(),
      currency: selectedPricingReferenceCurrency(),
    } : { id: state.pricingReferenceId || "", source: state.pricingReferenceSource || "bundled", tax: selectedPricingReferenceTax(), currency: selectedPricingReferenceCurrency() },
    quote_session: currentQuoteSessionPayload({
      quoteGenerated: Boolean(state.basisConfirmed || state.outputRows.length),
      includeDraftState: true,
    }),
    analysis_mode: normalizeAnalysisMode(options.analysisMode || state.pendingAnalysisMode),
    generator_label: generator.label,
    images: state.images.slice(0, MAX_REFERENCE_IMAGES),
    confirmed: true,
    quote_date: elements.quoteDate.value,
    project_number: elements.projectNumber.value.trim(),
    client: {
      name: elements.clientName.value.trim(),
      attention: elements.clientAttention.value.trim(),
      title: elements.clientTitle.value.trim(),
      address: elements.clientAddress.value,
    },
    project,
    company: {
      name: elements.quoteCompanyName.value.trim(),
      header_details: elements.headerDetails.value,
      logo_data_url: state.headerLogo ? state.headerLogo.data_url : "",
      logo_name: state.headerLogo ? state.headerLogo.name : "",
      logo_type: state.headerLogo ? state.headerLogo.type : "",
    },
    tax: selectedPricingReferenceTax(),
    quote_tax: collectTaxDetails(),
    quote_currency: collectQuoteCurrency(),
    quote_exchange_rate: collectQuoteExchangeRate(),
    user_feedback: state.pendingFeedback,
    quote_basis: includeDraftContext ? { ...state.quoteBasis, ...quoteBasisFromSections(state.quoteBasisSections) } : {},
    quote_basis_sections: includeDraftContext ? cloneQuoteBasisSections(state.quoteBasisSections) : [],
    line_items: includeDraftContext ? (state.outputRows.length ? outputRowsToLineItems(state.outputRows) : state.lineItems) : [],
    analysis_findings: state.analysisFindings,
    blocking_clarification_questions: state.blockingClarificationQuestions,
    view_pdf: options.viewPdf === true,
    quote_text: {
      terms_heading: elements.termsHeading.value.trim(),
      payment_terms: splitLines(elements.paymentTerms.value),
      notes_heading: elements.notesHeading.value.trim(),
      standard_notes: splitLines(elements.standardNotes.value),
      acceptance_text: elements.acceptanceText.value.trim(),
      person_label: elements.personLabel.value.trim(),
      stamp_label: elements.stampLabel.value.trim(),
      date_label: elements.dateLabel.value.trim(),
    },
    signature: {
      company_signatory: elements.companySignatory.value.trim(),
      company_title: elements.companyTitle.value.trim(),
      company_date_label: elements.companyDateLabel.value.trim(),
    },
    rich_text: collectRichTextDetails(),
  };
}

function buildLineItemNormalizePayload() {
  const pricingReference = currentPricingReference();
  const profileId = generationProfileIdForPayload();
  return {
    profile_id: profileId,
    pricing_reference_id: pricingReference?.id || state.pricingReferenceId || "",
    pricing_reference: pricingReference ? {
      id: pricingReference.id || state.pricingReferenceId || "",
      label: pricingReference.label || "",
      source: pricingReference.source || "bundled",
      tax: pricingReference.tax || selectedPricingReferenceTax(),
      currency: selectedPricingReferenceCurrency(),
    } : {
      id: state.pricingReferenceId || "",
      source: "bundled",
      tax: selectedPricingReferenceTax(),
      currency: selectedPricingReferenceCurrency(),
    },
    project: {
      booth_width: state.boothDimensions.booth_width,
      booth_depth: state.boothDimensions.booth_depth,
      booth_size: state.boothDimensions.booth_size,
      dimension_source: state.boothDimensions.dimension_source,
    },
    quote_basis: { ...state.quoteBasis, ...quoteBasisFromSections(state.quoteBasisSections) },
    quote_basis_sections: cloneQuoteBasisSections(state.quoteBasisSections),
    line_items: state.lineItems.map(normalizeLineItem),
  };
}

function setResultStatus(label, tone = "") {
  elements.resultStatus.textContent = label;
  elements.resultStatus.classList.remove("is-ok", "is-warn", "is-bad");
  if (tone) elements.resultStatus.classList.add(tone);
}

function renderMessages(messages = [], tone = "") {
  elements.messageList.innerHTML = messages
    .map((message) => `<div class="message ${tone}">${escapeHtml(message)}</div>`)
    .join("");
}

function setDownloadFiles(files = []) {
  const excelFile = files.find((file) => /\.xlsx$/i.test(file.name || "")) || null;
  const pdfFile = files.find((file) => /\.pdf$/i.test(file.name || "")) || null;
  state.downloadFileRevision = excelFile ? revisionNumber(state.outputRevision, 0) : -1;
  state.pdfFileRevision = pdfFile ? revisionNumber(state.outputRevision, 0) : -1;
  state.downloadFile = excelFile ? { ...excelFile, output_revision: state.downloadFileRevision } : null;
  state.pdfFile = pdfFile ? { ...pdfFile, output_revision: state.pdfFileRevision } : null;
  updateDownloadButton();
}

function revisionNumber(value, fallback = 0) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.trunc(number);
}

function markOutputRowsDirty() {
  state.outputRevision = revisionNumber(state.outputRevision, 0) + 1;
  setDownloadFiles([]);
}

function downloadFileIsFresh(file = state.downloadFile) {
  if (!file?.url) return false;
  const fileRevision = revisionNumber(state.downloadFileRevision, -1);
  return fileRevision >= 0 && fileRevision === revisionNumber(state.outputRevision, 0);
}

function pdfFileIsFresh(file = state.pdfFile) {
  if (!file?.url) return false;
  const fileRevision = revisionNumber(state.pdfFileRevision, -1);
  return fileRevision >= 0 && fileRevision === revisionNumber(state.outputRevision, 0);
}

function quoteSessionHasFreshOutputExports() {
  return downloadFileIsFresh(state.downloadFile) || pdfFileIsFresh(state.pdfFile);
}

function appIsBusy() {
  return state.isAnalysisRunning || state.isGenerating || state.isPreparingOutput || state.quoteSessionRestoreBusy || state.quoteSessionDashboardBusy;
}

function appBusyTitle() {
  if (state.quoteSessionDashboardBusy) return "Dashboard is loading.";
  if (state.quoteSessionRestoreBusy) return "Quote session is loading.";
  if (state.isAnalysisRunning) return "Analysis is running.";
  if (state.isPreparingOutput) return "Output is being prepared.";
  if (state.isGenerating) return "Quotation generation is running.";
  return "";
}

function waitForUiPaint() {
  return new Promise((resolve) => {
    let settled = false;
    let fallbackId = 0;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (fallbackId && typeof window.clearTimeout === "function") window.clearTimeout(fallbackId);
      resolve();
    };
    fallbackId = window.setTimeout(finish, 120);
    if (typeof window.requestAnimationFrame !== "function") {
      finish();
      return;
    }
    window.requestAnimationFrame(() => window.requestAnimationFrame(finish));
  });
}

function updateDownloadButton() {
  if (!elements.sideDownloadButton && !elements.sideViewPdfButton) return;
  const freshFile = downloadFileIsFresh(state.downloadFile) ? state.downloadFile : null;
  const freshPdfFile = elements.sideViewPdfButton && pdfFileIsFresh(state.pdfFile) ? state.pdfFile : null;
  const validation = outputRowsValid();
  const enabled = state.activeSidePanel === "output" && validation.valid && !state.isGenerating && !state.isPreparingOutput;
  if (elements.sideDownloadButton) {
    elements.sideDownloadButton.classList.toggle("is-disabled", !enabled);
    elements.sideDownloadButton.setAttribute("aria-disabled", String(!enabled));
    elements.sideDownloadButton.tabIndex = enabled ? 0 : -1;
    elements.sideDownloadButton.href = enabled && freshFile?.url ? freshFile.url : "#";
    elements.sideDownloadButton.download = freshFile?.url ? freshFile.name || "quotation.xlsx" : "";
    elements.sideDownloadButton.textContent = "Download Excel";
  }
  if (elements.sideViewPdfButton) {
    elements.sideViewPdfButton.classList.toggle("is-disabled", !enabled);
    elements.sideViewPdfButton.setAttribute("aria-disabled", String(!enabled));
    elements.sideViewPdfButton.tabIndex = enabled ? 0 : -1;
    elements.sideViewPdfButton.href = enabled && freshPdfFile?.url ? freshPdfFile.url : "#";
    elements.sideViewPdfButton.textContent = "View PDF";
  }
}

function showExcelGeneratingModal(options = {}) {
  if (!elements.excelGeneratingModal) return;
  elements.excelGeneratingEyebrow.textContent = options.eyebrow || "Quotation export";
  elements.excelGeneratingTitle.textContent = options.title || "Generating Excel";
  elements.excelGeneratingMessage.textContent = options.message || "Preparing the quotation workbook.";
  if (elements.excelGeneratingSpinner) elements.excelGeneratingSpinner.hidden = false;
  if (elements.excelGeneratingActions) elements.excelGeneratingActions.hidden = true;
  if (elements.excelGeneratingActionButton) elements.excelGeneratingActionButton.dataset.exportAction = "";
  if (["excel_ready", "pdf_ready"].includes(state.restorableOverlay)) {
    state.restorableOverlay = "";
    saveWorkspaceViewState();
  }
  elements.excelGeneratingModal.classList.remove("is-ready");
  elements.excelGeneratingModal.hidden = false;
  elements.excelGeneratingModal.classList.add("is-open");
}

function hideExcelGeneratingModal() {
  if (!elements.excelGeneratingModal) return;
  elements.excelGeneratingModal.classList.remove("is-open", "is-ready");
  elements.excelGeneratingModal.hidden = true;
  if (elements.excelGeneratingActions) elements.excelGeneratingActions.hidden = true;
  if (elements.excelGeneratingActionButton) elements.excelGeneratingActionButton.dataset.exportAction = "";
  if (["excel_ready", "pdf_ready"].includes(state.restorableOverlay)) {
    state.restorableOverlay = "";
    saveSessionState();
  }
}

function showGeneratedExportReadyModal(viewPdf = false) {
  const file = viewPdf
    ? (pdfFileIsFresh(state.pdfFile) ? state.pdfFile : null)
    : (downloadFileIsFresh(state.downloadFile) ? state.downloadFile : null);
  if (!file?.url || !elements.excelGeneratingModal) return false;
  elements.excelGeneratingEyebrow.textContent = "Quotation export";
  elements.excelGeneratingTitle.textContent = viewPdf ? "PDF ready" : "Excel ready";
  elements.excelGeneratingMessage.textContent = viewPdf
    ? "Your PDF is ready. Click Open PDF to view it in a new tab."
    : "Your workbook is ready. Click Download Excel to save it.";
  if (elements.excelGeneratingSpinner) elements.excelGeneratingSpinner.hidden = true;
  if (elements.excelGeneratingActions) elements.excelGeneratingActions.hidden = false;
  if (elements.excelGeneratingActionButton) {
    elements.excelGeneratingActionButton.textContent = viewPdf ? "Open PDF" : "Download Excel";
    elements.excelGeneratingActionButton.dataset.exportAction = viewPdf ? "pdf" : "excel";
  }
  elements.excelGeneratingModal.hidden = false;
  elements.excelGeneratingModal.classList.add("is-open", "is-ready");
  state.restorableOverlay = viewPdf ? "pdf_ready" : "excel_ready";
  saveSessionState();
  elements.excelGeneratingActionButton?.focus();
  return true;
}

function handleGeneratedExportReadyAction() {
  const action = elements.excelGeneratingActionButton?.dataset.exportAction || "";
  const handled = action === "pdf" ? viewCurrentPdfFile() : action === "excel" ? downloadCurrentExcelFile() : false;
  if (handled) hideExcelGeneratingModal();
}

function generatedExportReadyModalIsOpen() {
  return Boolean(
    elements.excelGeneratingModal
    && !elements.excelGeneratingModal.hidden
    && elements.excelGeneratingModal.classList.contains("is-ready")
  );
}

function dismissGeneratedExportReadyModal() {
  if (!generatedExportReadyModalIsOpen()) return false;
  hideExcelGeneratingModal();
  return true;
}

function generationLoadingModalOptions(viewPdf = false) {
  return viewPdf
    ? {
        eyebrow: "Quotation export",
        title: "Generating PDF",
        message: "Building the workbook first, then preparing its PDF export.",
      }
    : {
        eyebrow: "Quotation export",
        title: "Regenerating Excel",
        message: "Building the workbook from the current reviewed rows.",
      };
}

function generationFinalizingModalOptions(viewPdf = false) {
  return {
    eyebrow: "Quotation export",
    title: viewPdf ? "Finalizing PDF" : "Finalizing Excel",
    message: viewPdf
      ? "Saving the generated PDF to this quote before it is opened."
      : "Saving the generated workbook to this quote before download.",
  };
}

function clearActiveJob() {
  state.activeJob = null;
  saveSessionState();
}


function downloadCurrentExcelFile(file = state.downloadFile) {
  if (!file?.url) return false;
  try {
    const link = document.createElement("a");
    link.href = file.url;
    link.download = file.name || "quotation.xlsx";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (error) {
    window.location.href = file.url;
  }
  return true;
}

function viewCurrentPdfFile(file = state.pdfFile) {
  if (!file?.url) return false;
  const opened = window.open(file.url, "_blank");
  if (opened) {
    opened.opener = null;
    return true;
  }
  const link = document.createElement("a");
  link.href = file.url;
  link.target = "_blank";
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  return true;
}

function pricingMatchStatus(row = {}) {
  const status = typeof row === "string" ? row : row.status;
  return String(status || "").trim().toLowerCase();
}

function pricingStatusLabel(status = "") {
  const labels = {
    matched: "Catalog match",
    "matched-from-ambiguous": "Ambiguous match selected",
    ambiguous: "Ambiguous match",
    "manual-display": "Manual display price",
    "quantity-review": "Quantity needs review",
    custom: "Custom manual price",
    "needs-pricing": "Needs pricing",
    unmatched: "Unmatched",
  };
  const normalized = pricingMatchStatus(status);
  return labels[normalized] || String(status || "").trim() || "Unknown";
}

function numberOrNull(value) {
  if (value === "" || value === null || value === undefined) return null;
  if (String(value).trim().toLowerCase() === "included") return null;
  const numeric = Number(String(value ?? "").replaceAll(",", "").trim());
  return Number.isFinite(numeric) ? numeric : null;
}

function orderNumber(value) {
  const number = numberOrNull(value);
  return number !== null && number > 0 ? Math.trunc(number) : null;
}

function unitPriceEditKind(value) {
  const text = String(value ?? "").trim();
  if (!text) return "blank";
  if (text.toLowerCase() === "included") return "included";
  return numberOrNull(text) === null ? "invalid" : "number";
}

function effectiveOutputUnitPrice(row = {}) {
  const overrideText = String(row.unit_price_override ?? "").trim();
  const manual = numberOrNull(overrideText);
  if (manual !== null) return manual;
  if (overrideText && overrideText.toLowerCase() !== "included") return null;
  return numberOrNull(row.catalog_unit_price);
}

function formatAmount(value) {
  if (value === "" || value === null || value === undefined) return "";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  return numeric.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function quoteAmountValue(value, multiplier = quoteFxMultiplier()) {
  const numeric = numberOrNull(value);
  if (numeric === null) return null;
  const fx = quoteFxMultiplier(multiplier);
  return Math.round(numeric * fx * 100) / 100;
}

function recalculateOutputRow(row = {}) {
  const quantity = numberOrNull(row.quantity);
  const priceMode = row.price_mode === "Included" ? "Included" : "Priced";
  const unitPrice = effectiveOutputUnitPrice({ ...row, price_mode: priceMode });
  const hasUsablePrice = unitPrice !== null && unitPrice >= 0;
  return {
    ...row,
    price_mode: priceMode,
    amount: priceMode === "Included" ? 0 : (quantity !== null && hasUsablePrice ? Math.round(quantity * unitPrice * 100) / 100 : ""),
  };
}

function outputComparableText(value = "") {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function isInformationalDimensionText(value = "") {
  const rawText = cleanCustomerQuoteLineText(value);
  if (bracketedCatalogReferenceParts(rawText)) return false;
  const text = rawText.toLowerCase();
  const startsAsDimensionNote = /^\s*(?:use\s+)?(?:a\s+|the\s+)?(?:booth|stand|space)\s+(?:footprint|dimensions?|size)\b/.test(text)
    || /^\s*(?:booth\s+)?floor\s+area\b/.test(text)
    || (text.includes("area takeoff") && /\b(?:booth|stand|space)\b/.test(text));
  if (!startsAsDimensionNote) return false;
  return /\b\d+(?:\.\d+)?\s*(?:m(?:w|d)?|sqm|sqft|ft)\b/.test(text)
    || /\b\d+(?:\.\d+)?\s*[x\u00d7]\s*\d+(?:\.\d+)?\b/.test(text);
}

function basisLineIsInformationalDimension(line = {}) {
  if (line.pricing_keyword || line.pricing_reference_description || line.catalog_description) return false;
  return isInformationalDimensionText(line.text || line.description || "");
}

function outputRowIsInformationalDimension(row = {}) {
  if (row.pricing_keyword || row.pricing_reference_description || row.catalog_description) return false;
  return isInformationalDimensionText(row.description || "");
}

function outputRowSectionMatchesBasis(row = {}, sectionTitle = "") {
  if (!String(sectionTitle || "").trim()) return true;
  const basisSection = sectionTitleKey(normalizeQuoteBasisTitle(sectionTitle));
  if (!basisSection) return true;
  const rowSection = sectionTitleKey(row.section);
  if (!rowSection) return true;
  return rowSection === basisSection;
}

function outputRowCoversBasisLine(row = {}, lineText = "", sectionTitle = "") {
  if (!outputRowSectionMatchesBasis(row, sectionTitle)) return false;
  const rowText = outputComparableText(row.description);
  const basisText = outputComparableText(lineText);
  if (!rowText || !basisText) return false;
  if (rowText.includes(basisText) || basisText.includes(rowText)) return true;
  const basisWords = basisText.split(" ").filter((word) => word.length > 3);
  if (basisWords.length < 4) return false;
  const rowWords = new Set(rowText.split(" "));
  const overlap = basisWords.filter((word) => rowWords.has(word)).length;
  return overlap / basisWords.length >= 0.85;
}

function outputRowCoversBasisEntry(row = {}, line = {}, sectionTitle = "") {
  const lineIds = [line.id, line.source_line_item_id].map((value) => String(value || "").trim()).filter(Boolean);
  const sourceId = String(row.source_basis_line_id || "").trim();
  if (sourceId && lineIds.length) return lineIds.includes(sourceId);
  const keywordMatch = row.pricing_keyword && line.pricing_keyword && String(row.pricing_keyword) === String(line.pricing_keyword);
  if (keywordMatch) return true;
  return outputRowCoversBasisLine(row, line.text || "", sectionTitle)
    || outputRowCoversBasisLine(row, line.catalog_description || "", sectionTitle);
}

function basisLineAllowsOutput(line = {}) {
  const basisTag = normalizeBasisTag(line.tag);
  if (basisTag === "Exclude" || basisTag === "Confirm") return false;
  if (basisTag === "Include") return true;
  return isCustomPricingBasisLine(line) && Boolean(line.custom_confirmed);
}

function outputRowAllowedByBasis(row = {}) {
  if (outputRowIsInformationalDimension(row)) return false;
  const reviewedSections = normalizeQuoteBasisSections(state.quoteBasisSections);
  const hasReviewedBasis = reviewedSections.some((section) => (section.lines || []).length > 0);
  const sections = hasReviewedBasis ? reviewedSections : basisSections(state.quoteBasisSections);
  const sourceId = String(row.source_basis_line_id || "").trim();
  let matched = false;
  let allowed = false;
  sections.forEach((section) => {
    (section.lines || []).forEach((line) => {
      const lineIds = [line.id, line.source_line_item_id].map((value) => String(value || "").trim()).filter(Boolean);
      const sourceMatch = sourceId && lineIds.includes(sourceId);
      const keywordMatch = row.pricing_keyword && line.pricing_keyword && String(row.pricing_keyword) === String(line.pricing_keyword);
      const sectionTitle = section.title || section.id || "";
      if (sourceId && lineIds.length && !sourceMatch) return;
      const textMatch = outputRowCoversBasisLine(row, line.text || "", sectionTitle) || outputRowCoversBasisLine(row, line.catalog_description || "", sectionTitle);
      if (!sourceMatch && !keywordMatch && !textMatch) return;
      matched = true;
      allowed = allowed || basisLineAllowsOutput(line);
    });
  });
  return hasReviewedBasis ? matched && allowed : !matched || allowed;
}

function basisOrderValue(sectionIndex = 0, lineIndex = 0) {
  return (sectionIndex + 1) * 100000 + lineIndex + 1;
}

function matchingAllowedBasisEntryForOutputRow(row = {}) {
  const reviewedSections = normalizeQuoteBasisSections(state.quoteBasisSections);
  const hasReviewedBasis = reviewedSections.some((section) => (section.lines || []).length > 0);
  if (!hasReviewedBasis) return null;
  const sourceId = String(row.source_basis_line_id || "").trim();
  for (let sectionIndex = 0; sectionIndex < reviewedSections.length; sectionIndex += 1) {
    const section = reviewedSections[sectionIndex];
    const lines = section.lines || [];
    for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
      const line = lines[lineIndex];
      if (!basisLineAllowsOutput(line)) continue;
      const lineIds = [line.id, line.source_line_item_id].map((value) => String(value || "").trim()).filter(Boolean);
      const sourceMatch = sourceId && lineIds.includes(sourceId);
      const keywordMatch = row.pricing_keyword && line.pricing_keyword && String(row.pricing_keyword) === String(line.pricing_keyword);
      const sectionTitle = section.title || section.id || "";
      if (sourceId && lineIds.length && !sourceMatch) continue;
      const textMatch = outputRowCoversBasisLine(row, line.text || "", sectionTitle)
        || outputRowCoversBasisLine(row, line.catalog_description || "", sectionTitle);
      if (sourceMatch || keywordMatch || textMatch) {
        return {
          line,
          section,
          sectionIndex,
          lineIndex,
          basis_order: basisOrderValue(sectionIndex, lineIndex),
        };
      }
    }
  }
  return null;
}

function matchingAllowedBasisLineForOutputRow(row = {}) {
  return matchingAllowedBasisEntryForOutputRow(row)?.line || null;
}

function inheritBasisOutputFieldsForEntry(row = {}, basisEntry = null) {
  const line = basisEntry?.line;
  if (!line) return row;
  const next = { ...row };
  next.basis_order = basisEntry.basis_order;
  if (line.quantity !== undefined && line.quantity !== null && String(line.quantity).trim() !== "") {
    next.quantity = line.quantity;
  }
  if (normalizeUnit(line.unit || "")) {
    next.unit = normalizeUnit(line.unit);
  }
  if (line.id || line.source_line_item_id) {
    next.source_basis_line_id = line.id || line.source_line_item_id;
  }
  if ((next.catalog_unit_price === "" || next.catalog_unit_price === null || next.catalog_unit_price === undefined) && line.catalog_unit_price !== undefined && line.catalog_unit_price !== null && String(line.catalog_unit_price).trim() !== "") {
    next.catalog_unit_price = line.catalog_unit_price;
  }
  if (line.pricing_keyword) {
    next.pricing_keyword = line.pricing_keyword;
    const description = outputCatalogDescription(line);
    if (description) next.description = description;
  }
  if (line.catalog_description && (line.pricing_keyword || !next.catalog_description)) {
    next.catalog_description = line.catalog_description;
  }
  if (line.pricing_reference_description && (line.pricing_keyword || !next.pricing_reference_description)) {
    next.pricing_reference_description = line.pricing_reference_description;
  }
  return normalizeOutputRow(next);
}

function inheritBasisOutputFields(row = {}) {
  return inheritBasisOutputFieldsForEntry(row, matchingAllowedBasisEntryForOutputRow(row));
}

function outputRowDedupeKey(row = {}) {
  const sourceId = String(row.source_basis_line_id || "").trim();
  if (sourceId) return `source:${sourceId}`;
  return [
    sectionTitleKey(row.section),
    outputComparableText(row.description),
    String(row.pricing_keyword || "").trim(),
    String(row.quantity || "").trim(),
    normalizeUnit(row.unit || ""),
  ].join("|");
}

function dedupeOutputRows(rows = []) {
  const seen = new Set();
  const deduped = [];
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const key = outputRowDedupeKey(row);
    if (key && seen.has(key)) return;
    if (key) seen.add(key);
    deduped.push(row);
  });
  return deduped;
}

function outputRowBasisMatchRank(row = {}, basisEntry = null, basisSourceIds = new Set()) {
  const line = basisEntry?.line;
  const sectionTitle = basisEntry?.section?.title || basisEntry?.section?.id || "";
  if (!line || !outputRowSectionMatchesBasis(row, sectionTitle)) return 0;
  const lineIds = [line.id, line.source_line_item_id].map((value) => String(value || "").trim()).filter(Boolean);
  const sourceId = String(row.source_basis_line_id || "").trim();
  if (sourceId && lineIds.includes(sourceId)) return 4;
  if (sourceId && basisSourceIds.has(sourceId)) return 0;
  const keywordMatch = row.pricing_keyword && line.pricing_keyword && String(row.pricing_keyword) === String(line.pricing_keyword);
  if (keywordMatch) return 3;
  if (outputRowCoversBasisLine(row, line.text || "", sectionTitle)
    || outputRowCoversBasisLine(row, line.catalog_description || "", sectionTitle)) return 2;
  return 0;
}

function outputRowFromBasisEntry(basisEntry = null) {
  const line = basisEntry?.line;
  const section = basisEntry?.section;
  if (!line || !section) return null;
  const customPricing = isCustomPricingBasisLine(line);
  const text = String(line.text || "").trim();
  if (!text) return null;
  const description = customPricing ? text : outputCatalogDescription(line);
  return normalizeOutputRow({
    section: normalizeCategoryTitle(section.title || section.id || "Quote Basis"),
    description,
    quantity: line.quantity ?? "",
    unit: normalizeUnit(line.unit || ""),
    price_mode: "Priced",
    unit_price_override: "",
    catalog_unit_price: line.catalog_unit_price ?? "",
    catalog_description: line.catalog_description || "",
    pricing_reference_description: line.pricing_reference_description || "",
    pricing_keyword: line.pricing_keyword || "",
    source_basis_line_id: line.id || line.source_line_item_id || "",
    category_order: line.category_order ?? "",
    item_order: line.item_order ?? "",
    basis_order: basisEntry.basis_order,
    status: customPricing ? "custom" : "needs-pricing",
  });
}

function outputRowsAlignedToBasis(generatedRows = []) {
  const normalizedSections = normalizeQuoteBasisSections(state.quoteBasisSections);
  const hasReviewedBasis = normalizedSections.some((section) => (section.lines || []).length > 0);
  if (!hasReviewedBasis) {
    return dedupeOutputRows(generatedRows.filter(outputRowAllowedByBasis).map(inheritBasisOutputFields));
  }
  const entries = quoteBasisOutputEntries(normalizedSections);
  const basisSourceIds = new Set(entries.flatMap(({ line }) => [line.id, line.source_line_item_id])
    .map((value) => String(value || "").trim())
    .filter(Boolean));
  const usedIndexes = new Set();
  return entries.map((basisEntry) => {
    let bestIndex = -1;
    let bestRank = 0;
    generatedRows.forEach((row, index) => {
      if (usedIndexes.has(index)) return;
      const rank = outputRowBasisMatchRank(row, basisEntry, basisSourceIds);
      if (rank > bestRank) {
        bestIndex = index;
        bestRank = rank;
      }
    });
    if (bestIndex >= 0) {
      usedIndexes.add(bestIndex);
      return inheritBasisOutputFieldsForEntry(generatedRows[bestIndex], basisEntry);
    }
    return outputRowFromBasisEntry(basisEntry);
  }).filter(Boolean);
}

function includedBasisOutputRows(existingRows = []) {
  const rows = [];
  quoteBasisOutputEntries().forEach((basisEntry) => {
    const { line, section } = basisEntry;
    const sectionTitle = section.title || section.id || "";
    const duplicate = existingRows.some((row) => outputRowCoversBasisEntry(row, line, sectionTitle))
      || rows.some((row) => outputRowCoversBasisEntry(row, line, sectionTitle));
    if (duplicate) return;
    const row = outputRowFromBasisEntry(basisEntry);
    if (row) rows.push(row);
  });
  return rows;
}

function outputRowFromLineItem(item = {}) {
  const normalized = normalizeLineItem(item);
  const description = normalized.pricing_keyword ? outputCatalogDescription(normalized) : normalized.description;
  return normalizeOutputRow({
    section: normalized.section,
    description,
    quantity: normalized.quantity,
    unit: normalized.unit,
    price_mode: normalized.price_mode,
    unit_price_override: normalized.unit_price_override || "",
    catalog_unit_price: normalized.catalog_unit_price || "",
    catalog_description: normalized.catalog_description || "",
    pricing_reference_description: normalized.pricing_reference_description || "",
    pricing_keyword: normalized.pricing_keyword,
    source_basis_line_id: normalized.source_basis_line_id,
    category_order: normalized.category_order,
    item_order: normalized.item_order,
    basis_order: normalized.basis_order,
    status: normalized.status || "",
  });
}

function outputQuantityPartsFromPricingMatch(row = {}) {
  const rawQuantity = String(row.quantity || "").trim();
  const explicitUnit = normalizeUnit(row.unit || "");
  if (!rawQuantity) return { quantity: "", unit: explicitUnit };
  if (explicitUnit) {
    const escapedUnit = explicitUnit.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const quantity = rawQuantity.replace(new RegExp(`\\s+${escapedUnit}$`, "i"), "").trim();
    return { quantity: quantity || rawQuantity, unit: explicitUnit };
  }
  const match = rawQuantity.match(/^(-?\d+(?:\.\d+)?)\s+(.+)$/);
  if (match) return { quantity: match[1], unit: normalizeUnit(match[2]) };
  return { quantity: rawQuantity, unit: "" };
}

function outputRowFromPricingMatch(row = {}) {
  const status = pricingMatchStatus(row);
  const amount = String(row.amount ?? "").trim();
  const quantityParts = outputQuantityPartsFromPricingMatch(row);
  const manualDisplayAmount = status === "manual-display" ? numberOrNull(amount) : null;
  const quantity = numberOrNull(quantityParts.quantity);
  const manualDisplayUnitPrice = manualDisplayAmount !== null
    ? (quantity !== null && quantity > 0 ? Math.round((manualDisplayAmount / quantity) * 100) / 100 : manualDisplayAmount)
    : "";
  const unitPrice = manualDisplayAmount !== null ? manualDisplayUnitPrice : row.unit_price || row.unit_price_override || "";
  const referenceDescription = pricingReferenceLineText(row.pricing_reference_description || row.catalog_description || "");
  return normalizeOutputRow({
    section: row.section,
    description: cleanCustomerQuoteLineText(row.description || row.catalog_description || ""),
    quantity: quantityParts.quantity,
    unit: quantityParts.unit,
    price_mode: status === "included" ? "Included" : "Priced",
    unit_price_override: status === "manual-price" || status === "manual-display" ? unitPrice : "",
    catalog_unit_price: status === "manual-price" || status === "manual-display" ? "" : unitPrice,
    catalog_description: row.catalog_description || "",
    pricing_reference_description: referenceDescription,
    pricing_keyword: row.keyword || row.pricing_keyword || "",
    source_basis_line_id: row.source_basis_line_id || "",
    category_order: row.category_order ?? "",
    item_order: row.item_order ?? "",
    status: row.status,
  });
}

function categoryOrderValue(row = {}) {
  return orderNumber(row.category_order) || 999999;
}

function pricingReferenceOrder(row = {}, fallbackIndex = 0) {
  return [
    categoryOrderValue(row),
    orderNumber(row.item_order) || 999999,
    fallbackIndex,
  ];
}

function compareOrderValues(left = [], right = []) {
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const difference = (left[index] || 0) - (right[index] || 0);
    if (difference) return difference;
  }
  return 0;
}

function sortOutputRows(rows = state.outputRows) {
  const copy = rows.map((row, index) => ({ row, index }));
  const compareText = (value) => String(value || "").toLowerCase();
  copy.sort((a, b) => {
    const sectionCompare = compareText(a.row.section).localeCompare(compareText(b.row.section));
    const descriptionCompare = compareText(a.row.description).localeCompare(compareText(b.row.description));
    if (state.outputSortMode === "pricing_reference") {
      const aBasisOrder = orderNumber(a.row.basis_order);
      const bBasisOrder = orderNumber(b.row.basis_order);
      if (aBasisOrder !== null || bBasisOrder !== null) {
        return compareOrderValues([
          aBasisOrder ?? 999999,
          categoryOrderValue(a.row),
          orderNumber(a.row.item_order) || 999999,
          a.index,
        ], [
          bBasisOrder ?? 999999,
          categoryOrderValue(b.row),
          orderNumber(b.row.item_order) || 999999,
          b.index,
        ])
          || sectionCompare
          || descriptionCompare
          || a.index - b.index;
      }
      return compareOrderValues(pricingReferenceOrder(a.row, a.index), pricingReferenceOrder(b.row, b.index))
        || sectionCompare
        || descriptionCompare
        || a.index - b.index;
    }
    if (state.outputSortMode === "name") {
      return descriptionCompare || a.index - b.index;
    }
    if (state.outputSortMode === "category") return sectionCompare || a.index - b.index;
    return sectionCompare || descriptionCompare || a.index - b.index;
  });
  return copy.map((item) => item.row);
}

function resetOutputSortModeToPricingReference() {
  state.outputSortMode = "pricing_reference";
  if (typeof elements !== "undefined" && elements.outputSortMode) elements.outputSortMode.value = state.outputSortMode;
}

function outputCellDisplayValue(row = {}, field = "") {
  if (field === "price_mode") return row.price_mode === "Included" ? "Included" : "Priced";
  if (field === "unit_price_override") {
    if (row.price_mode === "Included") return "Included";
    if (unitPriceEditKind(row.unit_price_override) === "invalid") return "???";
    if (numberOrNull(row.unit_price_override) !== null) return formatAmount(quoteAmountValue(row.unit_price_override));
    if (numberOrNull(row.catalog_unit_price) !== null) return formatAmount(quoteAmountValue(row.catalog_unit_price));
    return "???";
  }
  if (field === "amount") {
    if (row.price_mode === "Included") return "0.00";
    return formatAmount(quoteAmountValue(row.amount)) || "???";
  }
  return String(row[field] ?? "").trim() || "???";
}

function outputEditorPlaceholder(field = "") {
  const placeholders = {
    section: "e.g. AV equipment",
    description: "e.g. LED screen, truss, projector, chairs",
    quantity: "e.g. 2",
    unit: "e.g. unit",
    unit_price_override: "e.g. 150.00",
    amount: "e.g. 300.00",
    pricing_reference: "e.g. AV rental reference",
    note: "e.g. Internal notes, not shown to customer",
    notes: "e.g. Internal notes, not shown to customer",
  };
  return placeholders[field] || "e.g. Returning client adjustment";
}

function outputEditorHtml(row = {}, index = 0, field = "") {
  const value = field === "unit_price_override"
    ? row.price_mode === "Included"
      ? "Included"
      : String(row.unit_price_override || row.catalog_unit_price || "")
    : String(row[field] ?? "");
  if (field === "description") {
    return `<textarea class="output-cell-input output-description-input is-editing" data-output-editor-field="${field}" data-output-row="${index}" rows="3" placeholder="${escapeHtml(outputEditorPlaceholder(field))}">${escapeHtml(value)}</textarea>`;
  }
  if (field === "price_mode") {
    return `
      <select class="output-cell-input is-editing" data-output-editor-field="${field}" data-output-row="${index}">
        <option value="Priced" ${row.price_mode === "Included" ? "" : "selected"}>Priced</option>
        <option value="Included" ${row.price_mode === "Included" ? "selected" : ""}>Included</option>
      </select>
    `;
  }
  if (field === "unit_price_override") {
    const includedButton = row.price_mode !== "Included"
      ? `<button class="output-included-button" type="button" data-output-included-action="true" data-output-row="${index}" aria-label="Mark row ${index + 1} unit price as included" title="Mark unit price as Included">Included</button>`
      : "";
    return `
      <span class="output-unit-price-editor">
        <input class="output-cell-input is-editing" data-output-editor-field="${field}" data-output-row="${index}" value="${escapeHtml(value)}" inputmode="decimal" placeholder="${escapeHtml(outputEditorPlaceholder(field))}">
        ${includedButton}
      </span>
    `;
  }
  const inputMode = field === "quantity" ? "decimal" : "text";
  return `<input class="output-cell-input is-editing" data-output-editor-field="${field}" data-output-row="${index}" value="${escapeHtml(value)}" inputmode="${inputMode}" placeholder="${escapeHtml(outputEditorPlaceholder(field))}">`;
}

function renderOutputEditCell(row = {}, index = 0, field = "", extraClass = "") {
  const display = outputCellDisplayValue(row, field);
  const pending = display === "???";
  const unitPriceCell = field === "unit_price_override";
  const label = ({
    section: "Section",
    description: "Description",
    quantity: "Quantity",
    unit: "Unit",
    unit_price_override: "Unit price",
    amount: "Amount",
  })[field] || field;
  const className = [
    "output-edit-cell",
    extraClass,
    unitPriceCell ? "output-unit-price-cell" : "",
    pending ? "is-pending" : "",
  ].filter(Boolean).join(" ");
  const cellBody = unitPriceCell
    ? `<span class="output-unit-price-content"><span class="output-cell-text">${escapeHtml(display)}</span></span>`
    : `<span class="output-cell-text">${escapeHtml(display)}</span>`;
  return `
    <td class="${className}" data-output-label="${escapeHtml(label)}" data-output-edit-field="${field}" data-output-row="${index}" tabindex="0" title="Click to edit">
      ${cellBody}
    </td>
  `;
}

function ensureOutputRowsFromLineItems() {
  if (state.outputRows.length) return;
  refreshOutputRowsFromLineItems();
}

function refreshOutputRowsFromLineItems() {
  resetOutputSortModeToPricingReference();
  const generatedRows = state.lineItems.map(outputRowFromLineItem);
  state.outputRows = sortOutputRows(outputRowsAlignedToBasis(generatedRows));
}

function snapshotOutputRows(rows = state.outputRows) {
  return rows.map((row) => normalizeOutputRow({ ...row }));
}

function outputRowsToLineItems(rows = state.outputRows) {
  return rows.map((row) => {
    const next = {
      section: String(row.section || "").trim(),
      description: String(row.description || "").trim(),
      quantity: row.quantity,
      unit: normalizeUnit(row.unit || ""),
      output_unit_locked: true,
      pricing_keyword: row.pricing_keyword || "",
      price_mode: row.price_mode === "Included" ? "Included" : "Priced",
      source_basis_line_id: row.source_basis_line_id || "",
    };
    if (!next.source_basis_line_id) delete next.source_basis_line_id;
    const catalogUnitPrice = numberOrNull(row.catalog_unit_price);
    if (catalogUnitPrice !== null) next.catalog_unit_price = catalogUnitPrice;
    const catalogDescription = cleanCustomerQuoteLineText(row.catalog_description || "");
    if (catalogDescription) next.catalog_description = catalogDescription;
    const pricingReferenceDescription = pricingReferenceLineText(row.pricing_reference_description || "");
    if (pricingReferenceDescription) next.pricing_reference_description = pricingReferenceDescription;
    const categoryOrder = orderNumber(row.category_order);
    if (categoryOrder !== null) next.category_order = categoryOrder;
    const itemOrder = orderNumber(row.item_order);
    if (itemOrder !== null) next.item_order = itemOrder;
    const basisOrder = orderNumber(row.basis_order);
    if (basisOrder !== null) next.basis_order = basisOrder;
    const status = String(row.status || "").trim();
    if (status) next.status = status;
    if (next.price_mode === "Included") {
      next.display_price = "Included";
    } else {
      const unitPrice = numberOrNull(row.unit_price_override);
      if (unitPrice !== null) next.unit_price_override = unitPrice;
    }
    return next;
  });
}

function outputRowsValid(rows = state.outputRows) {
  const errors = [];
  rows.forEach((row, index) => {
    const label = `Row ${index + 1}`;
    if (!String(row.description || "").trim()) errors.push(`${label}: Description is required.`);
    const quantity = numberOrNull(row.quantity);
    if (!String(row.unit || "").trim()) errors.push(`${label}: Unit is required.`);
    if (row.price_mode !== "Included" && (quantity === null || quantity <= 0)) {
      errors.push(`${label}: Quantity must be greater than 0.`);
    }
    if (row.price_mode !== "Included") {
      const unitPrice = numberOrNull(row.unit_price_override);
      const catalogUnitPrice = numberOrNull(row.catalog_unit_price);
      const unitPriceKind = unitPriceEditKind(row.unit_price_override);
      if (unitPriceKind === "invalid") {
        errors.push(`${label}: Unit price must be a number or Included.`);
      } else if (unitPrice !== null && unitPrice < 0) {
        errors.push(`${label}: Unit price must be 0 or greater.`);
      } else if (unitPrice === null && catalogUnitPrice === null) {
        errors.push(`${label}: Unit price is required.`);
      }
    }
  });
  return { valid: errors.length === 0 && rows.length > 0, errors };
}

function outputDeleteRowLabel(index) {
  if (!Number.isInteger(index) || index < 0 || !state.outputRows[index]) return;
  const row = state.outputRows[index];
  const description = String(row.description || "").trim();
  return description || `Row ${index + 1}`;
}

function hideOutputDeleteModal() {
  state.outputDeleteRowIndex = -1;
  if (elements.outputDeleteModal) {
    elements.outputDeleteModal.classList.remove("is-open");
    elements.outputDeleteModal.hidden = true;
  }
}

function renderOutputDeleteModal() {
  const modal = elements.outputDeleteModal;
  if (!modal) return;
  const label = outputDeleteRowLabel(state.outputDeleteRowIndex);
  if (!label) {
    hideOutputDeleteModal();
    return;
  }
  if (elements.outputDeleteTitle) elements.outputDeleteTitle.textContent = "Delete output row?";
  if (elements.outputDeleteText) {
    elements.outputDeleteText.textContent = `Delete output row "${label}"?`;
  }
  modal.hidden = false;
  modal.classList.add("is-open");
  queueActionButtonFocus(elements.confirmOutputDeleteButton);
}

function requestOutputRowDelete(index) {
  if (appIsBusy()) return;
  if (!Number.isInteger(index) || index < 0 || !state.outputRows[index]) return;
  state.outputDeleteRowIndex = index;
  renderOutputDeleteModal();
}

function confirmOutputRowDelete() {
  const index = state.outputDeleteRowIndex;
  if (!Number.isInteger(index) || index < 0 || !state.outputRows[index]) {
    hideOutputDeleteModal();
    return;
  }
  state.outputRows.splice(index, 1);
  state.lineItems = outputRowsToLineItems();
  markOutputRowsDirty();
  hideOutputDeleteModal();
  const validation = outputRowsValid();
  renderPricingMatches(state.outputRows);
  renderMatchSummary({ pricing_matches: state.outputRows });
  renderOutputValidationMessages(validation.valid ? [] : validation.errors);
  syncControlStates();
  if (typeof queueQuoteSessionDraftStateSave === "function") queueQuoteSessionDraftStateSave({ quoteGenerated: true });
}

function deleteOutputRow(index) {
  requestOutputRowDelete(index);
}

function renderOutputValidationMessages(errors = state.outputErrors) {
  if (!elements.pricingReviewMessages) return;
  state.outputErrors = Array.isArray(errors)
    ? errors.map((error) => String(error || "").trim()).filter(Boolean)
    : [];
  elements.pricingReviewMessages.innerHTML = state.outputErrors
    .map((error) => `<div class="message warn">${escapeHtml(error)}</div>`)
    .join("");
}

function rowNeedsManualInput(row = {}) {
  if (!String(row.description || "").trim()) return true;
  if (row.price_mode === "Included") return false;
  const quantity = numberOrNull(row.quantity);
  const unitPrice = effectiveOutputUnitPrice(row);
  const hasPricingKeyword = Boolean(String(row.pricing_keyword || "").trim());
  const amountMissing = outputCellDisplayValue(row, "amount") === "???";
  if (quantity === null || quantity <= 0) return true;
  if (unitPrice === null || unitPrice < 0) return true;
  if (!hasPricingKeyword && numberOrNull(row.unit_price_override) === null) return true;
  return amountMissing;
}

function matchSummaryStats(rows = []) {
  const safeRows = Array.isArray(rows) ? rows : [];
  const sections = new Set(safeRows.map((row) => String(row.section || "").trim()).filter(Boolean)).size;
  const needsManualInput = safeRows.filter(rowNeedsManualInput).length;
  const pricedRows = safeRows.filter((row) => row.price_mode === "Included" || !rowNeedsManualInput(row)).length;
  const totalPending = needsManualInput > 0;
  const total = safeRows.reduce((sum, row) => {
    const amount = quoteAmountValue(row.amount);
    return Number.isFinite(amount) ? sum + amount : sum;
  }, 0);
  return { sections, pricedRows, needsManualInput, total, totalPending };
}

function formatSubtotalValue(stats = {}) {
  const total = Number(stats.total || 0);
  const totalText = `${collectQuoteCurrency()} ${total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return stats.totalPending ? `${totalText} + ???` : totalText;
}

function formatOutputTotalValue(stats = {}) {
  const subtotal = Number(stats.total || 0);
  const tax = collectTaxDetails();
  const taxRate = Number(tax.rate ?? DEFAULT_TAX_RATE);
  const grandTotal = Number.isFinite(taxRate) ? subtotal + (subtotal * taxRate) : subtotal;
  const totalText = `${collectQuoteCurrency()} ${grandTotal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return stats.totalPending ? `${totalText} + ???` : totalText;
}

function outputPricingSourceLabel() {
  const reference = currentPricingReference();
  if (!reference) return "Pricing reference";
  return reference.label || "Pricing reference";
}

function outputHeaderStatus(rows = state.outputRows) {
  const safeRows = Array.isArray(rows) ? rows : [];
  if (!safeRows.length) return { label: "Draft", className: "is-empty" };
  const stats = matchSummaryStats(safeRows);
  return stats.needsManualInput > 0
    ? { label: "Needs pricing", className: "is-warn" }
    : { label: "Ready", className: "is-ok" };
}

function updateOutputHeader(rows = state.outputRows) {
  const safeRows = Array.isArray(rows) ? rows : [];
  const status = outputHeaderStatus(safeRows);
  if (elements.outputStatusPill) {
    elements.outputStatusPill.textContent = status.label;
    elements.outputStatusPill.className = `output-status-pill ${status.className}`;
  }
  if (elements.outputSourceLabel) {
    elements.outputSourceLabel.textContent = outputPricingSourceLabel();
  }
  syncQuoteCommercialContextPills();
  if (elements.outputQuoteExchangeRate) {
    elements.outputQuoteExchangeRate.textContent = quoteExchangeRateText();
  }
  if (elements.outputTotalLines) {
    elements.outputTotalLines.innerHTML = `<strong>${safeRows.length}</strong> approved line${safeRows.length === 1 ? "" : "s"}`;
  }
}

function renderMatchSummary(result = {}) {
  const rows = result.pricing_matches || [];
  if (!rows.length) {
    elements.matchSummary.innerHTML = "";
    return;
  }
  const stats = matchSummaryStats(rows);
  const subtotalValue = formatSubtotalValue(stats);
  const totalValue = formatOutputTotalValue(stats);
  elements.matchSummary.innerHTML = `
    <div class="stat-card-row output-stat-card-row">
      <div class="stat-card">
        <span class="stat-card-icon blue" aria-hidden="true">S</span>
        <span class="stat-card-value">${stats.sections}</span>
        <span class="stat-card-label">Sections</span>
      </div>
      <div class="stat-card">
        <span class="stat-card-icon green" aria-hidden="true">#</span>
        <span class="stat-card-value">${stats.pricedRows}</span>
        <span class="stat-card-label">Priced rows</span>
      </div>
      <div class="stat-card">
        <span class="stat-card-icon amber" aria-hidden="true">$</span>
        <span class="stat-card-value">${subtotalValue}</span>
        <span class="stat-card-label">Subtotal</span>
      </div>
      <div class="stat-card">
        <span class="stat-card-icon amber" aria-hidden="true">$</span>
        <span class="stat-card-value">${totalValue}</span>
        <span class="stat-card-label">Total</span>
      </div>
    </div>
  `;
}

function renderPricingMatches(rows = [], options = {}) {
  state.pricingMatches = Array.isArray(rows) ? rows : [];
  if (options.fromPricingMatches) {
    state.outputRows = state.pricingMatches.map(outputRowFromPricingMatch);
  } else if (Array.isArray(rows) && rows.length && rows[0]?.price_mode) {
    state.outputRows = rows.map(normalizeOutputRow);
  }
  state.outputRows = sortOutputRows(state.outputRows);
  if (elements.outputSortMode) elements.outputSortMode.value = state.outputSortMode;
  const outputRows = state.outputRows;
  updateOutputHeader(outputRows);
  elements.pricingTableWrap.hidden = !outputRows.length;
  elements.pricingEmptyState.hidden = Boolean(outputRows.length) || Boolean(elements.pricingReviewMessages.innerHTML.trim());
  if (!outputRows.length) {
    elements.pricingMatchesBody.innerHTML = `<tr><td colspan="7">No output rows yet.</td></tr>`;
    updateDownloadButton();
    return;
  }
  elements.pricingMatchesBody.innerHTML = outputRows
    .map((row, index) => `
      <tr data-output-row="${index}">
        ${renderOutputEditCell(row, index, "section")}
        ${renderOutputEditCell(row, index, "description", "output-description-cell")}
        ${renderOutputEditCell(row, index, "quantity")}
        ${renderOutputEditCell(row, index, "unit")}
        ${renderOutputEditCell(row, index, "unit_price_override")}
        <td class="amount-cell ${outputCellDisplayValue(row, "amount") === "???" ? "is-pending" : ""}" data-output-label="Amount">${escapeHtml(outputCellDisplayValue(row, "amount"))}</td>
        <td class="output-row-actions" data-output-label="Delete">
          <button class="output-delete-button" type="button" data-output-delete-row="${index}" aria-label="Delete row">Del</button>
        </td>
      </tr>
    `)
    .join("");
  updateDownloadButton();
}

function clearPricingReviewMessages() {
  state.pricingIssues = [];
  state.outputErrors = [];
  elements.pricingReviewMessages.innerHTML = "";
  const pricingText = (elements.pricingMatchesBody.textContent || "").trim();
  const hasRows = Boolean(pricingText) && !/No pricing matches yet/i.test(pricingText);
  elements.pricingEmptyState.hidden = hasRows;
}

function handleOutputRowEdit(event) {
  const input = event.target.closest("[data-output-field]");
  if (!input) return;
  const index = Number(input.dataset.outputRow);
  const field = input.dataset.outputField;
  if (!Number.isInteger(index) || index < 0 || !state.outputRows[index] || !field) return;
  state.outputRows[index] = recalculateOutputRow({
    ...state.outputRows[index],
    [field]: input.value,
  });
  state.lineItems = outputRowsToLineItems();
  markOutputRowsDirty();
  const validation = outputRowsValid();
  renderOutputValidationMessages(validation.valid ? [] : validation.errors);
  renderPricingMatches(state.outputRows);
  syncControlStates();
  if (typeof queueQuoteSessionDraftStateSave === "function") queueQuoteSessionDraftStateSave({ quoteGenerated: true });
}

function commitOutputEditor(editor) {
  if (!editor || !editor.dataset.outputEditorField) return;
  if (editor.isConnected === false) return;
  const index = Number(editor.dataset.outputRow);
  const field = editor.dataset.outputEditorField;
  if (!Number.isInteger(index) || index < 0 || !state.outputRows[index] || !field) return;
  const currentRow = state.outputRows[index];
  let nextRow = { ...currentRow, [field]: editor.value };
  if (field === "unit_price_override") {
    const value = String(editor.value || "").trim();
    if (value.toLowerCase() === "included" || (currentRow.price_mode === "Included" && value === "")) {
      nextRow = { ...currentRow, price_mode: "Included", unit_price_override: "", display_price: "Included" };
    } else {
      nextRow = { ...currentRow, price_mode: "Priced", display_price: "", unit_price_override: value };
    }
  }
  state.outputRows[index] = recalculateOutputRow(nextRow);
  state.lineItems = outputRowsToLineItems();
  markOutputRowsDirty();
  const validation = outputRowsValid();
  renderOutputValidationMessages(validation.valid ? [] : validation.errors);
  renderPricingMatches(state.outputRows);
  renderMatchSummary({ pricing_matches: state.outputRows });
  syncControlStates();
  if (typeof queueQuoteSessionDraftStateSave === "function") queueQuoteSessionDraftStateSave({ quoteGenerated: true });
}

function applyOutputIncludedAction(button) {
  const index = Number(button?.dataset.outputRow);
  if (!Number.isInteger(index) || index < 0 || !state.outputRows[index]) return;
  state.outputRows[index] = recalculateOutputRow({
    ...state.outputRows[index],
    price_mode: "Included",
    unit_price_override: "",
    display_price: "Included",
  });
  state.lineItems = outputRowsToLineItems();
  markOutputRowsDirty();
  const validation = outputRowsValid();
  renderOutputValidationMessages(validation.valid ? [] : validation.errors);
  renderPricingMatches(state.outputRows);
  renderMatchSummary({ pricing_matches: state.outputRows });
  syncControlStates();
  if (typeof queueQuoteSessionDraftStateSave === "function") queueQuoteSessionDraftStateSave({ quoteGenerated: true });
}

function openOutputCellEditor(cell) {
  if (!cell || cell.querySelector("[data-output-editor-field]")) return;
  const index = Number(cell.dataset.outputRow);
  const field = cell.dataset.outputEditField;
  if (!Number.isInteger(index) || index < 0 || !state.outputRows[index] || !field) return;
  cell.innerHTML = outputEditorHtml(state.outputRows[index], index, field);
  const editor = cell.querySelector("[data-output-editor-field]");
  if (!editor) return;
  editor.focus();
  if (typeof editor.select === "function") editor.select();
}

function handleOutputCellClick(event) {
  const deleteButton = event.target.closest("[data-output-delete-row]");
  if (deleteButton) {
    event.preventDefault();
    deleteOutputRow(Number(deleteButton.dataset.outputDeleteRow));
    return;
  }
  const includedButton = event.target.closest("[data-output-included-action]");
  if (includedButton) {
    event.preventDefault();
    applyOutputIncludedAction(includedButton);
    return;
  }
  if (event.target.closest("[data-output-editor-field]")) return;
  const cell = event.target.closest(".output-edit-cell");
  if (!cell) return;
  openOutputCellEditor(cell);
}

function handleOutputIncludedPointerDown(event) {
  if (!event.target.closest("[data-output-included-action]")) return;
  event.preventDefault();
}

function handleOutputCellOpen(event) {
  const cell = event.target.closest(".output-edit-cell");
  if (!cell) return;
  openOutputCellEditor(cell);
}

function handleOutputCellKeydown(event) {
  const editor = event.target.closest("[data-output-editor-field]");
  if (editor) {
    if (event.key === "Escape") {
      event.preventDefault();
      renderPricingMatches(state.outputRows);
      return;
    }
    if (event.key === "Enter" && editor.tagName !== "TEXTAREA") {
      event.preventDefault();
      commitOutputEditor(editor);
    }
    return;
  }
  const cell = event.target.closest(".output-edit-cell");
  if (!cell) return;
  if (event.key === "Enter" || event.key === "F2") {
    event.preventDefault();
    openOutputCellEditor(cell);
  }
}

function handleOutputEditorCommit(event) {
  const editor = event.target.closest("[data-output-editor-field]");
  if (!editor) return;
  commitOutputEditor(editor);
}

function commitActiveOutputEditor() {
  const editor = elements.pricingMatchesBody?.querySelector("[data-output-editor-field]");
  if (!editor) return;
  commitOutputEditor(editor);
}

function renderBasisEmptyState(message = "Load images, complete Customer and Quote Company, then start analysis to review the draft here.") {
  elements.basisReviewSurface.innerHTML = `
    <div class="basis-empty-state">
      <strong>Quotation basis draft</strong>
      <p>${escapeHtml(message)}</p>
    </div>
  `;
}

function renderBasisFailureState(message = GENERIC_FAILURE_MESSAGE) {
  elements.basisReviewSurface.innerHTML = `
    <div class="basis-empty-state basis-empty-state-error">
      <strong>AI analysis did not complete</strong>
      <p>${escapeHtml(message)}</p>
    </div>
  `;
}

function clearBasisReviewSurface() {
  elements.basisReviewSurface.innerHTML = "";
}

function basisSections(sections = state.quoteBasisSections) {
  const normalized = normalizeQuoteBasisSections(sections.length ? sections : state.quoteBasis);
  return normalized.length
    ? normalized
    : [{ id: "draft", title: "Quote Basis", lines: [{ tag: "Confirm", text: "No detail generated yet." }] }];
}

function unresolvedConfirmLines(sections = state.quoteBasisSections) {
  return basisSections(sections)
    .flatMap((section) => (section.lines || [])
      .filter((line) => !basisLineIsInformationalDimension(line))
      .filter((line) => normalizeBasisTag(line.tag) === "Confirm" || isPendingAiProposalLine(line))
      .map((line) => `${section.title}: ${line.text}`));
}

function basisConfirmBlockReason(sections = state.quoteBasisSections) {
  if (quoteOutputProgressForNavigation()) return "";
  if ((state.blockingClarificationQuestions || []).some((question) => question.status !== "answered" && !String(question.answer || "").trim())) {
    return "Answer all clarification questions before confirming quotation basis.";
  }
  return unresolvedConfirmLines(sections).length
    ? "Resolve all review lines before confirming quotation basis."
    : "";
}

function firstUnresolvedBasisLineRef(sections = state.quoteBasisSections) {
  const normalizedSections = basisSections(sections);
  for (let sectionIndex = 0; sectionIndex < normalizedSections.length; sectionIndex += 1) {
    const section = normalizedSections[sectionIndex];
    const lines = Array.isArray(section.lines) ? section.lines : [];
    for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
      const line = lines[lineIndex];
      if (basisLineIsInformationalDimension(line)) continue;
      if (normalizeBasisTag(line.tag) !== "Confirm" && !isPendingAiProposalLine(line)) continue;
      return {
        sectionId: String(section.id || "").trim(),
        lineIndex,
      };
    }
  }
  return null;
}

function basisLineRowForRef(ref = {}) {
  if (!ref || !elements.basisReviewSurface) return null;
  const controls = Array.from(elements.basisReviewSurface.querySelectorAll("[data-basis-section][data-basis-line-index]"));
  const match = controls.find((control) => (
    String(control.dataset.basisSection || "") === String(ref.sectionId || "")
    && Number(control.dataset.basisLineIndex) === Number(ref.lineIndex)
  ));
  return match?.closest(".basis-line-row") || null;
}

function revealBlockedBasisAction() {
  const unresolvedRef = firstUnresolvedBasisLineRef();
  const unresolvedRow = basisLineRowForRef(unresolvedRef);
  if (unresolvedRow) {
    unresolvedRow.classList.remove("is-attention");
    void unresolvedRow.offsetWidth;
    unresolvedRow.classList.add("is-attention");
    unresolvedRow.scrollIntoView({ block: "center", behavior: "smooth" });
    window.setTimeout(() => unresolvedRow.classList.remove("is-attention"), 2600);
    return;
  }
  if (elements.aiFailureBanner && !elements.aiFailureBanner.hidden) {
    elements.aiFailureBanner.scrollIntoView({ block: "start", behavior: "smooth" });
  }
}

function showBlockedBasisAction(message = "Resolve the quotation basis before continuing.") {
  showBlockedAction(message, { details: false });
  revealBlockedBasisAction();
}

function basisTagLabel(tag = "") {
  const normalized = normalizeBasisTag(tag);
  if (normalized === "Custom") return "AI Proposal";
  return normalized;
}

function basisQuantityText(value = "") {
  if (value === undefined || value === null) return "";
  const text = String(value).replaceAll(",", "").trim();
  if (!text) return "";
  const match = text.match(/^-?\d+(?:\.\d+)?/);
  if (!match) return "";
  const quantity = Number(match[0]);
  if (!Number.isFinite(quantity) || quantity <= 0) return "";
  return quantity.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function basisQuantityLabel(line = {}) {
  const quantityText = basisQuantityText(line.quantity);
  if (!quantityText) return "";
  const unit = normalizeUnit(line.unit || "");
  return `${quantityText}${unit ? ` ${unit}` : ""}`;
}

function basisQuantityDisplayLabel(line = {}) {
  const quantityText = basisQuantityText(line.quantity);
  if (!quantityText) return "1 lot";
  let unit = normalizeUnit(line.unit || "").replace(/\s+length$/i, "").trim();
  if (/^nos?\.?$/i.test(unit)) unit = "nos.";
  return `${quantityText}${unit ? ` ${unit}` : ""}`;
}

function basisChatLineContext(line = {}) {
  const tag = normalizeBasisTag(line.tag);
  const text = String(line.text || "").trim();
  return `${tag}: ${text}`.trim();
}

function hasPricingReferenceDescription(line = {}) {
  return Boolean(pricingReferenceLineText(line.pricing_reference_description || ""));
}

function basisLinePillLabel(line = {}) {
  const tag = normalizeBasisTag(line.tag);
  if (isPendingAiProposalLine(line)) return "AI Confirm";
  return basisTagLabel(tag);
}

function basisLineAcceptsAsAiProposal(line = {}) {
  return isCustomPricingBasisLine(line);
}

function basisConfidenceLabel(line = {}) {
  const confidence = normalizeConfidence(line.confidence ?? line.confidence_pct);
  return `${confidence === null ? 50 : confidence}%`;
}

function basisTotalLineCount(sections = []) {
  return (Array.isArray(sections) ? sections : []).reduce((total, section) => {
    const lines = Array.isArray(section?.lines) ? section.lines : [];
    return total + lines.length;
  }, 0);
}

function quoteBasisOutputEntries(sections = state.quoteBasisSections) {
  return normalizeQuoteBasisSections(Array.isArray(sections) ? sections : [])
    .flatMap((section, sectionIndex) => (section.lines || [])
      .map((line, lineIndex) => ({
        line,
        section,
        sectionIndex,
        lineIndex,
        basis_order: basisOrderValue(sectionIndex, lineIndex),
      }))
      .filter((entry) => basisLineAllowsOutput(entry.line) && !basisLineIsInformationalDimension(entry.line)));
}

function basisOutputLineCount(sections = state.quoteBasisSections) {
  return quoteBasisOutputEntries(sections).length;
}

function basisTotalLineLabel(count = 0) {
  return `Total lines: ${Math.max(0, Number(count) || 0)}`;
}

function renderBasisConfirmSummary(sections = state.quoteBasisSections) {
  return "";
}

function basisCatalogReferenceTitle(line = {}) {
  return "";
}

function basisLineTitle(line = {}) {
  return basisCatalogReferenceTitle(line);
}

function basisPillTitle(line = {}, tag = "") {
  return basisCatalogReferenceTitle(line);
}

function catalogBackedBasisDisplayParts(line = {}) {
  const text = String(line.text || "").trim();
  const match = text.match(/^\[\s*([\s\S]*?)\s*\]\s*-\s*([\s\S]+)$/);
  if (!match) return null;
  const reference = String(match[1] || "").trim();
  const detail = String(match[2] || "").trim();
  if (!reference || !detail) return null;
  return { reference, detail };
}

function basisLineTextHtml(line = {}) {
  const displayParts = catalogBackedBasisDisplayParts(line);
  if (!displayParts) return escapeHtml(line.text);
  return `
    <span class="basis-line-catalog-display">
      <span class="basis-line-catalog-reference">[ ${escapeHtml(displayParts.reference)} ]</span>
      <span class="basis-line-catalog-detail"><span class="basis-line-catalog-arrow">--&gt;</span> ${escapeHtml(displayParts.detail)}</span>
    </span>
  `;
}

function basisPossibleMatchesHtml(section, line, index) {
  const matches = normalizePossiblePricingMatches(line.possible_pricing_matches || []);
  if (!matches.length || normalizeBasisTag(line.tag) === "Exclude" || !basisLineAcceptsAsAiProposal(line)) return "";
  return `
    <span class="basis-line-possible-matches" aria-label="Possible pricing reference matches">
      ${matches.map((match, matchIndex) => `
        <button class="basis-line-possible-match" type="button" data-basis-section="${escapeHtml(section.id)}" data-basis-line-index="${index}" data-basis-possible-match-index="${matchIndex}" title="Use this pricing reference match">
          <strong>Possible match:</strong> ${escapeHtml(match.description)}
        </button>
      `).join("")}
    </span>
  `;
}

function renderBasisLine(section, line, index) {
  const tag = normalizeBasisTag(line.tag);
  const customPricing = isCustomPricingBasisLine(line);
  const pendingAiProposal = isPendingAiProposalLine(line);
  const pillLabel = basisLinePillLabel(line);
  const acceptsAsAiProposal = basisLineAcceptsAsAiProposal(line);
  const quantityLabel = basisQuantityDisplayLabel(line);
  const confidenceLabel = basisConfidenceLabel(line);
  const pillTitle = basisPillTitle(line, tag);
  const lineTitle = basisLineTitle(line);
  const pillTitleAttribute = pillTitle ? ` title="${escapeHtml(pillTitle)}"` : "";
  const lineTitleAttribute = lineTitle ? ` title="${escapeHtml(lineTitle)}"` : "";
  const rowClasses = [
    "basis-line-row",
    `basis-line-${tag.toLowerCase()}`,
    customPricing ? "basis-line-custom-priced" : "",
    pendingAiProposal ? "basis-line-custom-confirm" : "",
    pillLabel === "AI Confirm" ? "basis-line-ai-confirm" : "",
  ].filter(Boolean).join(" ");
  const primaryAction = acceptsAsAiProposal
    ? `<button class="basis-line-tag-button" type="button" data-basis-section="${escapeHtml(section.id)}" data-basis-line-index="${index}" data-basis-tag="Custom" aria-label="Accept this AI proposal" title="Accept AI proposal">&#x2713;</button>`
    : `<button class="basis-line-tag-button" type="button" data-basis-section="${escapeHtml(section.id)}" data-basis-line-index="${index}" data-basis-tag="Include" aria-label="Mark this line as included" title="Mark included">&#x2713;</button>`;
  return `
    <li class="${escapeHtml(rowClasses)}">
      <span class="basis-line-icon" aria-hidden="true"></span>
      <span class="basis-line-meta">
        <span class="basis-line-pill"${pillTitleAttribute}>${escapeHtml(pillLabel)}</span>
        <span class="basis-confidence-pill" title="AI confidence">${escapeHtml(confidenceLabel)}</span>
        <span class="basis-quantity-text">${escapeHtml(quantityLabel)}</span>
      </span>
      <span class="basis-line-text"${lineTitleAttribute}>${basisLineTextHtml(line)}${basisPossibleMatchesHtml(section, line, index)}</span>
      <span class="basis-line-actions">
        ${primaryAction}
        <button class="basis-line-tag-button" type="button" data-basis-section="${escapeHtml(section.id)}" data-basis-line-index="${index}" data-basis-tag="Exclude" aria-label="Mark this line as excluded" title="Mark excluded">X</button>
        <button class="basis-line-tool" type="button" data-revise-section="${escapeHtml(section.id)}" data-revise-line-index="${index}" aria-label="Revise this line" title="Revise this line">Re</button>
      </span>
    </li>
  `;
}

function renderBasisTagLegend() {
  return `
    <div class="basis-tag-legend" aria-label="Quote basis tag meanings">
      <div class="basis-tag-legend-column basis-tag-legend-actions">
        ${BASIS_TAGS.filter(([tag]) => ["Include", "Exclude", "Custom"].includes(tag)).map(([tag, label, description]) => `
          <span class="basis-tag-legend-item basis-line-${escapeHtml(tag.toLowerCase())}">
            <strong class="basis-line-pill">${escapeHtml(label)}</strong>
            <span>${escapeHtml(description)}</span>
          </span>
        `).join("")}
      </div>
      <div class="basis-tag-legend-column basis-tag-legend-review">
        <span class="basis-tag-legend-item basis-line-confirm">
          <strong class="basis-line-pill">Confirm</strong>
          <span>Needs include, exclude, or revision</span>
        </span>
        <span class="basis-tag-legend-item basis-line-custom-confirm">
          <strong class="basis-line-pill">AI Confirm</strong>
          <span>AI proposal needs acceptance or revision</span>
        </span>
        <span class="basis-tag-legend-item basis-line-confirm">
          <strong class="basis-confidence-pill">92%</strong>
          <span>AI confidence level in quote basis line</span>
        </span>
      </div>
    </div>
  `;
}

function renderQuoteBasisMessage(basis = state.quoteBasis, source = "") {
  const aiFailed = source === "local" && state.aiFailed;
  if (aiFailed) {
    return `
      <div class="basis-empty-state basis-empty-state-error">
        <strong>AI analysis did not complete</strong>
        <p>${GENERIC_FAILURE_MESSAGE}</p>
      </div>
    `;
  }
  const statusText = aiFailed ? "AI failed" : source === "edited" ? "Edited draft" : "Needs review";
  const qualityPill = normalizeAnalysisMode(state.lastAnalysisMode) === ANALYSIS_MODE_HIGH_QUALITY
    ? `<span class="quote-basis-quality-pill">High Quality</span>`
    : "";
  const sections = basisSections(state.quoteBasisSections.length ? state.quoteBasisSections : normalizeQuoteBasisSections(basis));
  const reviewSections = sections
    .map((section) => ({
      ...section,
      reviewLineEntries: (section.lines || [])
        .map((line, index) => ({ line, index }))
        .filter((entry) => !basisLineIsInformationalDimension(entry.line)),
    }))
    .map((section) => ({ ...section, lines: section.reviewLineEntries.map((entry) => entry.line) }))
    .filter((section) => section.reviewLineEntries.length);
  const dimensionLines = sections.flatMap((section) => (section.lines || [])
    .filter((line) => basisLineIsInformationalDimension(line))
    .map((line) => cleanCustomerQuoteLineText(line.text || line.description || "")))
    .filter(Boolean);
  const dimensionDisplay = dimensionLines.length
    ? `
      <div class="basis-visual-display" aria-label="Booth display details">
        <strong>Booth Details</strong>
        <ul>
          ${dimensionLines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}
        </ul>
      </div>
    `
    : "";
  const reviewLineCount = basisTotalLineCount(reviewSections);
  const outputLineCount = basisOutputLineCount(sections);
  return `
    <div class="assistant-card quote-basis-card ${aiFailed ? "quote-basis-card-failed" : ""}">
      <div class="quote-basis-header">
        <div>
          <div class="quote-basis-title-row">
            <h3>Quote Basis</h3>
            <span>${escapeHtml(statusText)}</span>
            ${qualityPill}
          </div>
          <p>${aiFailed ? GENERIC_FAILURE_MESSAGE : "Please review the AI takeoff and revise individual lines where needed."}</p>
        </div>
        <div class="quote-basis-source">
          <span class="pricing-reference-source-line" aria-label="Selected pricing reference context">
            <strong class="pricing-reference-source-name">${aiFailed ? "Local fallback only" : escapeHtml(outputPricingSourceLabel())}</strong>
            ${aiFailed ? "" : quoteCommercialContextPillsHtml()}
            <span class="pricing-reference-divider" aria-hidden="true"></span>
            <span class="pricing-reference-line-count"><strong>${reviewLineCount}</strong> review line${reviewLineCount === 1 ? "" : "s"}</span>
            <span class="pricing-reference-divider" aria-hidden="true"></span>
            <span class="pricing-reference-line-count"><strong>${outputLineCount}</strong> output line${outputLineCount === 1 ? "" : "s"}</span>
          </span>
        </div>
      </div>
      ${renderAnalysisFindings()}
      ${renderBasisConfirmSummary(sections)}
      ${renderBasisTagLegend()}
      ${dimensionDisplay}
      ${reviewSections.length ? `
        <div class="basis-review-grid">
          ${reviewSections.map((section) => `
          <div class="basis-review-item">
            <div class="basis-section-heading">
              <h4>${escapeHtml(section.title)}</h4>
              <span class="basis-section-actions">
                <button class="basis-section-action-button" type="button" data-basis-section="${escapeHtml(section.id)}" data-basis-section-action="Include" aria-label="Accept all review lines in ${escapeHtml(section.title)}" title="Accept all review lines">&#x2713;</button>
                <button class="basis-section-action-button" type="button" data-basis-section="${escapeHtml(section.id)}" data-basis-section-action="Exclude" aria-label="Exclude all review lines in ${escapeHtml(section.title)}" title="Exclude all review lines">X</button>
                <span class="basis-section-action-spacer" aria-hidden="true"></span>
              </span>
            </div>
            <ul class="basis-line-list">
              ${section.reviewLineEntries.map((entry) => renderBasisLine(section, entry.line, entry.index)).join("")}
            </ul>
          </div>
          `).join("")}
        </div>
      ` : ""}
    </div>
  `;
}

function basisFieldLabel(field) {
  return basisDisplayTitle(state.quoteBasisSections.find((section) => section.id === field)?.title)
    || BASIS_FIELDS.find(([key]) => key === field)?.[1]
    || "Quote basis";
}

function updateQuoteBasisCard(source = "edited") {
  elements.basisReviewSurface.innerHTML = renderQuoteBasisMessage(state.quoteBasis, source);
}

function possibleMatchBasisDetailText(line = {}) {
  const text = cleanCustomerQuoteLineText(line.text || line.description || "");
  return text.replace(/^\s*(?:include|confirm|custom|manual|extra|needs pricing|ai confirm)\s*[-:]\s*/i, "").trim();
}

function catalogBackedPossibleMatchText(line = {}, match = {}) {
  const reference = pricingReferenceLineText(match.description || match.pricing_reference_description || match.catalog_description || "");
  const detail = possibleMatchBasisDetailText(line) || reference;
  return `[ ${reference} ] - ${detail}`;
}

function applyPossiblePricingMatch(sectionId, lineIndex, matchIndex) {
  if (!sectionId) return;
  const sections = cloneQuoteBasisSections(state.quoteBasisSections);
  const section = sections.find((item) => item.id === sectionId);
  const lineNumber = Number(lineIndex);
  const selectedIndex = Number(matchIndex);
  if (!section || !Number.isInteger(lineNumber) || !section.lines[lineNumber]) return;
  const currentLine = section.lines[lineNumber];
  const matches = normalizePossiblePricingMatches(currentLine.possible_pricing_matches || currentLine.possible_catalog_matches || []);
  if (!Number.isInteger(selectedIndex) || !matches[selectedIndex]) return;
  const match = matches[selectedIndex];
  const reference = pricingReferenceLineText(match.description);
  const nextLine = {
    ...currentLine,
    tag: "Include",
    text: catalogBackedPossibleMatchText(currentLine, match),
    pricing_keyword: match.pricing_keyword,
    catalog_description: reference,
    pricing_reference_description: reference,
  };
  if (match.unit) nextLine.unit = match.unit;
  if (match.catalog_unit_price !== undefined && match.catalog_unit_price !== null && String(match.catalog_unit_price).trim() !== "") {
    nextLine.catalog_unit_price = match.catalog_unit_price;
  }
  delete nextLine.custom_pricing;
  delete nextLine.custom_confirmed;
  delete nextLine.custom;
  delete nextLine.manual_pricing;
  delete nextLine.pricing_tag;
  delete nextLine.pricing_status;
  delete nextLine.possible_pricing_matches;
  delete nextLine.possible_catalog_matches;
  section.lines[lineNumber] = nextLine;
  state.quoteBasisSections = sections;
  state.quoteBasis = quoteBasisFromSections(sections);
  state.basisConfirmed = false;
  if (Array.isArray(state.outputRows) && state.outputRows.length && Array.isArray(state.lineItems)) {
    refreshOutputRowsFromLineItems();
    state.lineItems = outputRowsToLineItems();
    markOutputRowsDirty();
    if (typeof renderPricingMatches === "function") renderPricingMatches(state.outputRows);
    if (typeof renderMatchSummary === "function") renderMatchSummary({ pricing_matches: state.outputRows });
    if (typeof renderOutputValidationMessages === "function") renderOutputValidationMessages(outputRowsValid().errors);
  } else {
    setDownloadFiles([]);
  }
  updateQuoteBasisCard("edited");
  syncControlStates();
}

function retagBasisLine(sectionId, lineIndex, nextTag) {
  const requestedTag = normalizeBasisTag(nextTag);
  if (!sectionId || !["Include", "Custom", "Exclude"].includes(requestedTag)) return;
  const sections = cloneQuoteBasisSections(state.quoteBasisSections);
  const section = sections.find((item) => item.id === sectionId);
  const index = Number(lineIndex);
  if (!section || !section.lines[index]) return;
  const currentLine = section.lines[index];
  const resolvedTag = requestedTag === "Include" && basisLineAcceptsAsAiProposal(currentLine) ? "Custom" : requestedTag;
  const customPricing = isCustomPricingBasisLine(currentLine) || resolvedTag === "Custom";
  const customConfirmed = customPricing && ["Include", "Custom"].includes(requestedTag);
  section.lines[index] = {
    ...currentLine,
    tag: resolvedTag,
    ...(customPricing ? { custom_pricing: true } : {}),
    ...(customConfirmed ? { custom_confirmed: true } : { custom_confirmed: false }),
  };
  state.quoteBasisSections = sections;
  state.quoteBasis = quoteBasisFromSections(sections);
  state.basisConfirmed = false;
  markOutputRowsDirty();
  updateQuoteBasisCard("edited");
  syncControlStates();
}

function retagBasisSectionConfirmLines(sectionId, nextTag) {
  if (!sectionId || !["Include", "Exclude"].includes(nextTag)) return;
  const sections = cloneQuoteBasisSections(state.quoteBasisSections);
  const section = sections.find((item) => item.id === sectionId);
  if (!section) return;
  let changed = false;
  section.lines = (section.lines || []).map((line) => {
    const resolvedTag = nextTag === "Include" && basisLineAcceptsAsAiProposal(line) ? "Custom" : nextTag;
    const customPricing = isCustomPricingBasisLine(line) || resolvedTag === "Custom";
    const customConfirmed = customPricing && nextTag === "Include";
    const currentTag = normalizeBasisTag(line.tag);
    if (currentTag === resolvedTag && (!customPricing || Boolean(line.custom_confirmed) === customConfirmed)) return line;
    changed = true;
    return {
      ...line,
      tag: resolvedTag,
      ...(customPricing ? { custom_pricing: true, custom_confirmed: customConfirmed } : { custom_confirmed: false }),
    };
  });
  if (!changed) return;
  state.quoteBasisSections = sections;
  state.quoteBasis = quoteBasisFromSections(sections);
  state.basisConfirmed = false;
  markOutputRowsDirty();
  updateQuoteBasisCard("edited");
  syncControlStates();
}

function basisChatIntroMessage() {
  return "Tell me what to change. I will draft the full replacement sentence for approval before applying it.";
}

function selectedBasisLine() {
  const section = state.quoteBasisSections.find((item) => item.id === state.basisChat.sectionId);
  return section?.lines?.[state.basisChat.lineIndex] || null;
}

function appendBasisChatMessage(role, text, options = {}) {
  const message = document.createElement("div");
  message.className = `basis-chat-message ${role}`;
  message.innerHTML = `
    <span class="basis-chat-label">${role === "user" ? "You" : "Assistant"}</span>
    ${renderPlainText(text)}
    ${options.proposalActions ? `
      <div class="basis-chat-message-actions">
        <button class="secondary-button" type="button" data-basis-chat-action="discard">Discard</button>
        <button class="primary-button" type="button" data-basis-chat-action="apply">Apply</button>
      </div>
    ` : ""}
  `;
  elements.basisChatMessages.appendChild(message);
  elements.basisChatMessages.scrollTop = elements.basisChatMessages.scrollHeight;
}

function appendBasisChatTyping() {
  const message = document.createElement("div");
  message.className = "basis-chat-message assistant is-typing";
  message.dataset.basisChatTyping = "true";
  message.innerHTML = `
    <span class="basis-chat-label">Assistant</span>
    <span class="basis-chat-typing-row" role="status" aria-label="Assistant is typing">
      <span class="basis-chat-typing-dots" aria-hidden="true">
        <i></i><i></i><i></i>
      </span>
      <span class="ai-elapsed basis-chat-elapsed" id="basisChatElapsed" aria-live="polite">Elapsed 0:00</span>
    </span>
  `;
  elements.basisChatMessages.appendChild(message);
  elements.basisChatMessages.scrollTop = elements.basisChatMessages.scrollHeight;
  return message;
}

function removeBasisChatTyping(message) {
  if (message?.parentNode) message.remove();
}

function resetBasisChatProposal() {
  state.basisChat.proposal = null;
  elements.basisChatProposal.hidden = true;
  elements.basisChatProposal.innerHTML = "";
  elements.basisChatProposalActions.hidden = true;
  elements.basisChatApplyButton.disabled = true;
  elements.basisChatKeepButton.disabled = true;
}

function proposalChangedFields(proposal) {
  const nextSections = normalizeQuoteBasisSections(proposal?.quoteBasisSections || proposal?.quoteBasis || {});
  const currentSections = cloneQuoteBasisSections(state.quoteBasisSections);
  const currentById = new Map(currentSections.map((section) => [section.id, section]));
  return nextSections
    .filter((section) => JSON.stringify(currentById.get(section.id) || null) !== JSON.stringify(section))
    .map((section) => section.title);
}

function proposalLineDelta(proposal) {
  if (state.basisChat.scope !== "line" || !state.basisChat.sectionId) return null;
  const nextSections = normalizeQuoteBasisSections(proposal?.quoteBasisSections || proposal?.quoteBasis || {});
  const section = nextSections.find((item) => item.id === state.basisChat.sectionId);
  const nextLine = section?.lines?.[state.basisChat.lineIndex];
  const currentLine = selectedBasisLine() || parseBasisLine(state.basisChat.line);
  if (!nextLine || !currentLine || nextLine.text === currentLine.text && nextLine.tag === currentLine.tag) return null;
  return {
    currentTag: normalizeBasisTag(currentLine.tag),
    currentConfidence: normalizeConfidence(currentLine.confidence ?? currentLine.confidence_pct),
    currentPricingReferenceDescription: currentLine.pricing_reference_description || "",
    currentText: currentLine.text,
    nextTag: normalizeBasisTag(nextLine.tag),
    nextConfidence: normalizeConfidence(nextLine.confidence ?? nextLine.confidence_pct),
    nextPricingReferenceDescription: nextLine.pricing_reference_description || "",
    nextText: nextLine.text,
  };
}

function renderProposalSectionChips(changedFields = []) {
  if (!changedFields.length) return "";
  const visible = changedFields.slice(0, 6);
  const remaining = changedFields.length - visible.length;
  return `
    <div class="basis-chat-proposal-meta">
      <span>Affected</span>
      <div class="basis-chat-proposal-chips">
        ${visible.map((field) => `<i>${escapeHtml(field)}</i>`).join("")}
        ${remaining > 0 ? `<i>+${remaining} more</i>` : ""}
      </div>
    </div>
  `;
}

function renderBasisChatProposalCard(proposal, changedFields = []) {
  const delta = proposalLineDelta(proposal);
  const message = proposal?.message || "Review this proposed line change before applying it.";
  return `
    <div class="basis-chat-proposal-header">
      <div>
        <span>Proposed change</span>
        <strong>Basis line update</strong>
      </div>
      <span class="basis-chat-proposal-status">Review</span>
    </div>
    <p class="basis-chat-proposal-summary">${escapeHtml(message)}</p>
    ${proposal.literalReplacement ? renderLiteralReplacementPreview(proposal) : delta ? `
      <div class="basis-chat-compare">
        <div class="basis-chat-compare-card">
          <span>Current</span>
          <p><strong>${escapeHtml(basisLinePillLabel({ tag: delta.currentTag, confidence: delta.currentConfidence, pricing_reference_description: delta.currentPricingReferenceDescription }))}</strong> ${escapeHtml(delta.currentText)}</p>
        </div>
        <div class="basis-chat-compare-card is-proposed">
          <span>Proposed</span>
          <p><strong>${escapeHtml(basisLinePillLabel({ tag: delta.nextTag, confidence: delta.nextConfidence, pricing_reference_description: delta.nextPricingReferenceDescription }))}</strong> ${escapeHtml(delta.nextText)}</p>
        </div>
      </div>
    ` : `
      <p class="basis-chat-proposal-summary">This proposal updates the selected basis line and keeps existing line items available for review.</p>
    `}
    ${renderProposalSectionChips(changedFields)}
  `;
}

function setBasisChatProposal(proposal) {
  state.basisChat.proposal = proposal;
  const changedFields = proposalChangedFields(proposal);
  elements.basisChatProposal.hidden = false;
  elements.basisChatProposal.innerHTML = renderBasisChatProposalCard(proposal, changedFields);
  elements.basisChatProposalActions.hidden = false;
  elements.basisChatApplyButton.disabled = false;
  elements.basisChatKeepButton.disabled = false;
  appendBasisChatMessage("assistant", "I drafted a proposed update below. Review it, then apply or discard.");
}

function basisChatFriendlyError(messages = []) {
  if (messages && typeof messages === "object" && !Array.isArray(messages)) {
    return genericFailureMessage(messages);
  }
  return genericFailureMessage();
}

function setBasisChatBusy(isBusy) {
  elements.basisChatPrompt.disabled = isBusy;
  elements.basisChatSendButton.disabled = isBusy;
  elements.basisChatApplyButton.disabled = isBusy || !state.basisChat.proposal;
  elements.basisChatKeepButton.disabled = isBusy || !state.basisChat.proposal;
}

function openBasisChatOverlay(scope = "line", options = {}) {
  if (scope !== "line") return;
  const preservedProposal = options.preserveState && state.basisChat?.proposal
    ? state.basisChat.proposal : null;
  state.basisChat = {
    scope,
    field: options.sectionId || options.field || "",
    sectionId: options.sectionId || options.field || "",
    lineIndex: Number.isInteger(options.lineIndex) ? options.lineIndex : -1,
    line: options.line || "",
    quantity: options.quantity ?? "",
    unit: options.unit || "",
    quantityLabel: options.quantityLabel || "",
    proposal: preservedProposal,
  };
  resetBasisChatProposal();
  elements.basisChatTitle.textContent = "Revise basis line";
  elements.basisChatContext.classList.toggle("has-selected-line", scope === "line");
  const line = selectedBasisLine() || parseBasisLine(state.basisChat.line);
  const tag = normalizeBasisTag(line.tag);
  const tagClass = `basis-chat-selected-tag-${tag.toLowerCase()}`;
  const tagLineClass = `basis-chat-selected-line-${tag.toLowerCase()}`;
  const quantityLabel = basisQuantityLabel(line);
  const displayQuantityLabel = basisQuantityDisplayLabel(line);
  const confidenceLabel = basisConfidenceLabel(line);
  const selectedTagTitle = basisPillTitle(line, tag);
  const selectedTagTitleAttribute = selectedTagTitle ? ` title="${escapeHtml(selectedTagTitle)}"` : "";
  state.basisChat.line = basisChatLineContext(line);
  state.basisChat.quantity = line.quantity ?? state.basisChat.quantity ?? "";
  state.basisChat.unit = normalizeUnit(line.unit || state.basisChat.unit || "");
  state.basisChat.quantityLabel = quantityLabel || state.basisChat.quantityLabel || "";
  elements.basisChatContext.innerHTML = `
    <span class="basis-chat-context-label">${escapeHtml(basisFieldLabel(state.basisChat.sectionId))}</span>
    <span class="basis-chat-selected-line ${escapeHtml(tagLineClass)}">
      <span class="basis-chat-selected-meta">
        <strong class="basis-chat-selected-tag ${escapeHtml(tagClass)}"${selectedTagTitleAttribute}>${escapeHtml(basisLinePillLabel(line))}</strong>
        <span class="basis-confidence-pill" title="AI confidence">${escapeHtml(confidenceLabel)}</span>
      </span>
      <span class="basis-chat-selected-body">
        <span class="basis-chat-selected-quantity">${escapeHtml(displayQuantityLabel)}</span>
        <span class="basis-chat-selected-text">${escapeHtml(line.text || state.basisChat.line)}</span>
      </span>
    </span>
  `;
  elements.basisChatPrompt.placeholder = "e.g. Add teardown notes or adjust LED screen size";
  elements.basisChatMessages.innerHTML = "";
  appendBasisChatMessage("assistant", basisChatIntroMessage());
  if (preservedProposal) setBasisChatProposal(preservedProposal);
  elements.basisChatPrompt.value = "";
  elements.basisChatOverlay.hidden = false;
  elements.basisChatOverlay.classList.add("is-open");
  document.body.classList.add("basis-chat-open");
  state.restorableOverlay = "basis_chat";
  saveSessionState();
  window.setTimeout(() => elements.basisChatPrompt.focus(), 0);
}

function restoreBasisChatOverlay() {
  const saved = { ...state.basisChat };
  if (!saved.sectionId || !Number.isInteger(Number(saved.lineIndex))) return false;
  openBasisChatOverlay("line", {
    sectionId: saved.sectionId,
    field: saved.field,
    lineIndex: Number(saved.lineIndex),
    line: saved.line,
    quantity: saved.quantity,
    unit: saved.unit,
    quantityLabel: saved.quantityLabel,
    preserveState: true,
  });
  return true;
}

function closeBasisChatOverlay() {
  elements.basisChatOverlay.classList.remove("is-open");
  elements.basisChatOverlay.hidden = true;
  document.body.classList.remove("basis-chat-open");
  resetBasisChatProposal();
  if (state.restorableOverlay === "basis_chat") {
    state.restorableOverlay = "";
    saveSessionState();
  }
}

function basisChatPayload(text) {
  return {
    ...buildPayload(),
    basis_chat: {
      question: text,
      scope: state.basisChat.scope,
      field: state.basisChat.sectionId || state.basisChat.field,
      line_index: state.basisChat.lineIndex,
      line: state.basisChat.line,
      quantity: state.basisChat.quantity,
      unit: state.basisChat.unit,
      quantity_label: state.basisChat.quantityLabel,
    },
  };
}

function normalizeServerBasisChatProposal(proposal = {}) {
  const quoteBasis = proposal.quoteBasis || proposal.quote_basis || {};
  const sections = mergeBasisProposalLineMetadata(
    normalizeQuoteBasisSections(proposal.quoteBasisSections || proposal.quote_basis_sections || quoteBasis),
    state.quoteBasisSections
  );
  return {
    message: String(proposal.message || "AI drafted a proposed quote basis update.").trim(),
    quoteBasis: { ...cloneQuoteBasis(quoteBasis), ...quoteBasisFromSections(sections) },
    quoteBasisSections: sections,
    lineItems: Array.isArray(proposal.lineItems || proposal.line_items)
      ? (proposal.lineItems || proposal.line_items).map(normalizeLineItem)
      : state.lineItems.map(normalizeLineItem),
  };
}

function parseLiteralReplacementCommand(text = "") {
  const match = String(text || "").trim().match(/^(?:change|replace|switch)(?:\s+all)?\s+(.+?)\s+(?:to|with)\s+(.+?)\s*$/i);
  if (!match) return null;
  const from = match[1].trim().replace(/^['"]|['"]$/g, "");
  const to = match[2].trim().replace(/^['"]|['"]$/g, "");
  if (!from || !to || from.length > 80 || to.length > 80) return null;
  return { from, to };
}

function replaceLiteralText(value = "", from = "", to = "") {
  if (!from) return { text: String(value || ""), changed: false };
  const pattern = new RegExp(from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  const original = String(value || "");
  const next = original.replace(pattern, to);
  return { text: next, changed: next !== original };
}

function unbracketedCatalogReferenceText(reference = "", detail = "") {
  const cleanedReference = cleanCustomerQuoteLineText(reference);
  const cleanedDetail = cleanCustomerQuoteLineText(detail);
  if (!cleanedReference) return cleanedDetail;
  return cleanedDetail ? `${cleanedReference} - ${cleanedDetail}` : cleanedReference;
}

function markBasisLineAsManualPricing(line = {}) {
  const next = { ...line, custom_pricing: true };
  const tag = normalizeBasisTag(next.tag);
  if (tag === "Include" || tag === "Custom") next.custom_confirmed = true;
  delete next.pricing_keyword;
  delete next.catalog_description;
  delete next.pricing_reference_description;
  delete next.catalog_unit_price;
  delete next.possible_pricing_matches;
  delete next.possible_catalog_matches;
  delete next.pricing_tag;
  delete next.pricing_status;
  return next;
}

function replaceBasisLineReferenceText(line = {}, from = "", to = "") {
  const bracketed = bracketedCatalogReferenceParts(line.text || "");
  const targetText = bracketed ? bracketed.reference : line.text || "";
  const replaced = replaceLiteralText(targetText, from, to);
  if (!replaced.changed) return { line, changed: false };
  const nextLine = {
    ...line,
    text: bracketed
      ? unbracketedCatalogReferenceText(replaced.text, bracketed.detail)
      : replaced.text,
  };
  return {
    line: bracketed ? markBasisLineAsManualPricing(nextLine) : nextLine,
    changed: true,
  };
}

function buildLiteralReplacementProposal(command) {
  const sections = cloneQuoteBasisSections(state.quoteBasisSections);
  let changedLineCount = 0;
  const affectedSectionIds = new Set();
  const snippets = [];
  sections.forEach((section) => {
    (section.lines || []).forEach((line, index) => {
      const replaced = replaceBasisLineReferenceText(line, command.from, command.to);
      if (!replaced.changed) return;
      snippets.push({ section: section.title, before: line.text, after: replaced.line.text });
      section.lines[index] = {
        ...replaced.line,
        tag: bracketedCatalogReferenceParts(line.text || "") ? normalizeBasisTag(line.tag) : "Confirm",
      };
      if (isCustomPricingBasisLine(section.lines[index])) section.lines[index].custom_pricing = true;
      changedLineCount += 1;
      affectedSectionIds.add(section.id);
    });
  });
  const legacyBasis = quoteBasisFromSections(sections);
  Object.keys(legacyBasis).forEach((key) => {
    legacyBasis[key] = replaceLiteralText(legacyBasis[key], command.from, command.to).text;
  });
  const lineItems = state.lineItems.map((item) => {
    const replaced = replaceLiteralText(item.description, command.from, command.to);
    return replaced.changed ? { ...item, description: replaced.text } : item;
  });
  const outputRows = state.outputRows.map((row) => {
    const replaced = replaceLiteralText(row.description, command.from, command.to);
    return replaced.changed ? recalculateOutputRow({ ...row, description: replaced.text }) : row;
  });
  const changedOutputRows = outputRows.filter((row, index) => row.description !== state.outputRows[index]?.description).length;
  if (!changedLineCount && !changedOutputRows && !lineItems.some((item, index) => item.description !== state.lineItems[index]?.description)) return null;
  return {
    message: `Literal replacement: changed ${changedLineCount} basis line${changedLineCount === 1 ? "" : "s"} across ${affectedSectionIds.size} section${affectedSectionIds.size === 1 ? "" : "s"}.`,
    literalReplacement: true,
    changedLineCount,
    affectedSectionCount: affectedSectionIds.size,
    snippets: snippets.slice(0, 5),
    quoteBasis: legacyBasis,
    quoteBasisSections: sections,
    lineItems,
    outputRows,
  };
}

function simpleBasisEditFragment(text = "") {
  const fragment = cleanCustomerQuoteLineText(text).replace(/^['"]|['"]$/g, "");
  if (!fragment || fragment.length > 60 || /[?]/.test(fragment)) return "";
  if (/^(?:change|replace|switch|make|set|update|revise|edit|remove|delete|include|exclude)\b/i.test(fragment)) return "";
  const words = fragment.match(/[A-Za-z0-9.]+/g) || [];
  if (!words.length || words.length > 6) return "";
  const hasEditSignal = /\d/.test(fragment)
    || /\b(?:mm|cm|sqm|sqft|ft|m|black|white|red|blue|green|yellow|grey|gray|teal|wood|glass|metal|laminate|fabric|vinyl|paint)\b/i.test(fragment);
  return hasEditSignal ? fragment : "";
}

function replaceReferenceDimensionToken(value = "", fragment = "") {
  const replacement = cleanCustomerQuoteLineText(fragment).replace(/\s+/g, "");
  const replacementMatch = replacement.match(/^(\d+(?:\.\d+)?)(mm|cm|sqm|sqft|ft|m)$/i);
  if (!replacementMatch) return null;
  const replacementUnit = replacementMatch[2].toLowerCase();
  const text = cleanCustomerQuoteLineText(value);
  const dimensionPattern = /\b\d+(?:\.\d+)?\s*(mm|cm|sqm|sqft|ft|m)\b/gi;
  const matches = Array.from(text.matchAll(dimensionPattern));
  if (!matches.length) return null;
  const sameUnitMatches = matches.filter((match) => String(match[1] || "").toLowerCase() === replacementUnit);
  const candidates = sameUnitMatches.length ? sameUnitMatches : matches;
  if (candidates.length !== 1) return null;
  const match = candidates[0];
  const start = match.index;
  const end = start + match[0].length;
  return `${text.slice(0, start)}${replacement}${text.slice(end)}`;
}

function basisChatRequestedQuantityValue(text = "") {
  const cleaned = cleanCustomerQuoteLineText(text).toLowerCase().replace(/,/g, "");
  const patterns = [
    /\b(?:qty|quantity|count)\s*(?:to|=|:|is|as)?\s*(\d+(?:\.\d+)?)\b/i,
    /\b(\d+(?:\.\d+)?)\s*(?:qty|quantity|count)\b/i,
    /\bfrom\s+\d+(?:\.\d+)?\s+to\s+(\d+(?:\.\d+)?)\b/i,
    /\b(?:make|set|change|update|revise)\s+(?:it|this|that|qty|quantity|count)?\s*(?:to\s+)?(\d+(?:\.\d+)?)\s*(?:nos?\.?|pcs?|pieces?|units?|lots?|sets?|each|ea|sqm)\b/i,
  ];
  for (const pattern of patterns) {
    const match = cleaned.match(pattern);
    if (!match) continue;
    const quantity = leadingNumber(match[1]);
    if (quantity !== null && quantity > 0) return formatQuantityNumber(quantity);
  }
  return null;
}

function basisLineTextHasLiteralQuantityWord(line = {}) {
  const bracketed = bracketedCatalogReferenceParts(line.text || "");
  if (!bracketed) return false;
  return /\b(?:qty|quantity)\b/i.test(cleanCustomerQuoteLineText(bracketed.reference || ""));
}

function buildSelectedLineFragmentReplacementProposal(text = "") {
  if (state.basisChat.scope !== "line") return null;
  const sections = cloneQuoteBasisSections(state.quoteBasisSections);
  const section = sections.find((item) => item.id === state.basisChat.sectionId);
  const lineIndex = Number(state.basisChat.lineIndex);
  if (!section || !Number.isInteger(lineIndex) || !section.lines[lineIndex]) return null;
  const currentLine = section.lines[lineIndex];
  const requestedQuantity = basisChatRequestedQuantityValue(text);
  if (requestedQuantity !== null && !basisLineTextHasLiteralQuantityWord(currentLine)) {
    const currentQuantity = leadingNumber(currentLine.quantity);
    if (currentQuantity === requestedQuantity) return null;
    const nextLine = { ...currentLine, quantity: requestedQuantity };
    if (!normalizeUnit(nextLine.unit || "") && normalizeUnit(state.basisChat.unit || "")) {
      nextLine.unit = state.basisChat.unit;
    }
    section.lines[lineIndex] = nextLine;
    const quoteBasis = quoteBasisFromSections(sections);
    const unit = normalizeUnit(nextLine.unit || "");
    return {
      message: `Change the selected line quantity to ${requestedQuantity}${unit ? ` ${unit}` : ""}?`,
      quoteBasis,
      quoteBasisSections: sections,
      lineItems: Array.isArray(state.lineItems) ? state.lineItems : [],
    };
  }
  const fragment = simpleBasisEditFragment(text);
  if (!fragment) return null;
  const bracketed = bracketedCatalogReferenceParts(currentLine.text || "");
  const targetText = bracketed ? bracketed.reference : currentLine.text || "";
  const replacedReference = replaceReferenceDimensionToken(targetText, fragment);
  if (!replacedReference || cleanCustomerQuoteLineText(replacedReference) === cleanCustomerQuoteLineText(targetText)) return null;
  let nextLine = {
    ...currentLine,
    text: bracketed
      ? unbracketedCatalogReferenceText(replacedReference, bracketed.detail)
      : replacedReference,
  };
  if (bracketed) nextLine = markBasisLineAsManualPricing(nextLine);
  section.lines[lineIndex] = nextLine;
  const quoteBasis = quoteBasisFromSections(sections);
  return {
    message: `Change the selected line to '${nextLine.text}'?`,
    quoteBasis,
    quoteBasisSections: sections,
    lineItems: Array.isArray(state.lineItems) ? state.lineItems : [],
  };
}

function renderLiteralReplacementPreview(proposal) {
  return `
    <div class="basis-chat-literal-stats">
      <span>Changed lines: <strong>${proposal.changedLineCount}</strong></span>
      <span>Affected sections: <strong>${proposal.affectedSectionCount}</strong></span>
    </div>
    <div class="basis-chat-compare">
      ${proposal.snippets.map((snippet) => `
        <div class="basis-chat-compare-card">
          <span>${escapeHtml(snippet.section)}</span>
          <p><strong>Before:</strong> ${escapeHtml(snippet.before)}</p>
          <p><strong>After:</strong> ${escapeHtml(snippet.after)}</p>
        </div>
      `).join("")}
    </div>
  `;
}

async function buildAiBasisChatResponse(text, options = {}) {
  const restoredOperation = options.resume === true ? normalizeActiveJob(options.operation || state.activeJob || {}) : null;
  if (!restoredOperation && !canStartAnalysis()) return null;
  const operation = restoredOperation?.type === "basis_chat" ? restoredOperation : normalizeActiveJob({
    id: newClientJobId(),
    type: "basis_chat",
    phase: "starting",
    startedAt: new Date().toISOString(),
    text,
  });
  state.activeJob = operation;
  const previousRunning = state.isAnalysisRunning;
  state.isAnalysisRunning = true;
  setBasisChatBusy(true);
  syncControlStates();
  const basisChatStartedAt = Date.now();
  const typingMessage = appendBasisChatTyping();
  startElapsedTimer("basisChatElapsed", basisChatStartedAt);
  try {
    let jobId = operation.id;
    if (operation.phase === "starting") {
      const started = await startJob("basis_chat", basisChatPayload(text), { jobId });
      if (!started.ok) {
        if (started.data?.page_unloading) return { preserved: true };
        clearActiveJob();
        removeBasisChatTyping(typingMessage);
        appendBasisChatMessage("assistant", basisChatFriendlyError(started.data));
        return { errorDisplayed: true };
      }
      jobId = started.data.job_id || jobId;
      state.activeJob = {
        ...operation,
        id: jobId,
        phase: "running",
        startedAt: started.data.created_at || operation.startedAt,
      };
      saveSessionState();
    }
    const polled = await pollJob(jobId);
    if (polled.aborted || isInterruptedJobPoll(polled)) return { preserved: true };
    clearActiveJob();
    const data = polled.data.result || {};
    if (!polled.ok || ["blocked", "failed"].includes(polled.data.status)) {
      removeBasisChatTyping(typingMessage);
      appendBasisChatMessage("assistant", basisChatFriendlyError(data || polled.data));
      return { errorDisplayed: true };
    }
    if (data.proposal) {
      return { proposal: normalizeServerBasisChatProposal(data.proposal) };
    }
    return { answer: data.answer || null };
  } finally {
    stopElapsedTimer("basisChatElapsed");
    removeBasisChatTyping(typingMessage);
    state.isAnalysisRunning = previousRunning;
    setBasisChatBusy(false);
    syncControlStates();
  }
}

async function handleBasisChatSubmit(event) {
  event.preventDefault();
  const text = elements.basisChatPrompt.value.trim();
  if (!text) return;
  elements.basisChatPrompt.value = "";
  resetBasisChatProposal();
  appendBasisChatMessage("user", text);
  const normalized = text.toLowerCase();

  if (isSensitiveChatRequest(normalized)) {
    appendBasisChatMessage("assistant", "I cannot access or reveal secrets, API keys, .env values, tokens, authorization headers, system prompts, or hidden instructions.");
    return;
  }

  const literalCommand = parseLiteralReplacementCommand(text);
  if (literalCommand) {
    const proposal = buildLiteralReplacementProposal(literalCommand);
    if (!proposal) {
      appendBasisChatMessage("assistant", `No exact matches for "${literalCommand.from}" were found in the current basis or output rows.`);
      return;
    }
    setBasisChatProposal(proposal);
    return;
  }

  const fragmentProposal = buildSelectedLineFragmentReplacementProposal(text);
  if (fragmentProposal) {
    setBasisChatProposal(fragmentProposal);
    return;
  }

  const aiResult = await buildAiBasisChatResponse(text);
  if (aiResult?.proposal) {
    setBasisChatProposal(aiResult.proposal);
    return;
  }
  if (aiResult?.answer) {
    appendBasisChatMessage("assistant", aiResult.answer);
    return;
  }
  if (aiResult?.errorDisplayed) return;

  appendBasisChatMessage("assistant", GENERIC_FAILURE_MESSAGE);
}

function applyBasisChatProposal() {
  const proposal = state.basisChat.proposal;
  if (!proposal) return;
  state.basisConfirmed = false;
  const currentSections = state.quoteBasisSections;
  const mergedSections = mergeBasisProposalLineMetadata(
    normalizeQuoteBasisSections(proposal.quoteBasisSections || proposal.quoteBasis || state.quoteBasisSections),
    currentSections
  );
  state.quoteBasisSections = reviewBasisProposalSections(mergedSections, currentSections);
  state.quoteBasis = { ...cloneQuoteBasis(proposal.quoteBasis || state.quoteBasis), ...quoteBasisFromSections(state.quoteBasisSections) };
  state.lineItems = Array.isArray(proposal.lineItems) ? proposal.lineItems.map(normalizeLineItem) : [];
  state.outputRows = [];
  state.originalOutputRows = [];
  state.outputErrors = [];
  setDownloadFiles([]);
  if (typeof renderPricingMatches === "function") renderPricingMatches([]);
  if (typeof renderMatchSummary === "function") renderMatchSummary({});
  if (typeof clearPricingReviewMessages === "function") clearPricingReviewMessages();
  updateQuoteBasisCard("edited");
  setSidePanel("basis", { force: true });
  resetBasisChatProposal();
  closeBasisChatOverlay();
  syncControlStates();
}

function keepCurrentBasis() {
  resetBasisChatProposal();
  appendBasisChatMessage("assistant", "Kept the current quote basis unchanged.");
}

function missingCustomerFields() {
  syncRichTextSources();
  const missing = [];
  const hasLines = (value) => splitLines(value).length > 0;
  if (!String(state.pricingReferenceId || "").trim() || !currentPricingReference()) missing.push("Quote Pricing Reference");
  if (!elements.clientName.value.trim()) missing.push("Client name");
  if (!elements.clientAttention.value.trim()) missing.push("Attention person");
  if (!elements.clientTitle.value.trim()) missing.push("Attention title");
  if (!hasLines(elements.clientAddress.value)) missing.push("Client address");
  if (!elements.projectTitle.value.trim()) missing.push("Quotation Title");
  if (!elements.showName.value.trim()) missing.push("Show name");
  if (!elements.quoteDate.value.trim()) missing.push("Quote date");
  if (!elements.projectNumber.value.trim()) missing.push("Project number");
  return missing;
}

function missingQuoteCompanyFields() {
  syncRichTextSources();
  const missing = [];
  const hasLines = (value) => splitLines(value).length > 0;
  if (!state.headerLogo?.data_url) missing.push("Header logo");
  if (!hasLines(elements.headerDetails.value)) missing.push("Header details");
  if (!elements.quoteCompanyName.value.trim()) missing.push("Quotation Company");
  if (!elements.acceptanceText.value.trim()) missing.push("Acceptance text");
  if (!elements.companySignatory.value.trim()) missing.push("Company signatory");
  if (!elements.companyTitle.value.trim()) missing.push("Signatory title");
  if (!elements.companyDateLabel.value.trim()) missing.push("Company date label");
  if (!elements.personLabel.value.trim()) missing.push("Person label");
  if (!elements.stampLabel.value.trim()) missing.push("Stamp label");
  if (!elements.dateLabel.value.trim()) missing.push("Date label");
  return missing;
}

function missingDetailFields() {
  return [...missingCustomerFields(), ...missingQuoteCompanyFields()];
}

function customerDetailsBlockReason(prefix = "Complete Customer details") {
  const missing = missingCustomerFields();
  return missing.length ? `${prefix}: ${missing.join(", ")}.` : "";
}

function quoteCompanyDetailsBlockReason(prefix = "Complete Quote Company details") {
  const missing = missingQuoteCompanyFields();
  return missing.length ? `${prefix}: ${missing.join(", ")}.` : "";
}

function firstMissingDetailsPanel() {
  return missingCustomerFields().length ? "customer" : "quote_company";
}

function startAnalysisBlockReason() {
  if (state.isAnalysisRunning) return "Analysis is already running.";
  if (state.isGenerating) return "Quotation generation is already running.";
  if (state.isPreparingOutput) return "Output is being prepared.";
  if (!hasReferenceFilesForNavigation()) return "Add at least one reference file before starting analysis.";
  if (!hasReferenceFilesForAnalysis()) return "Reference files from this saved quote are unavailable in this browser. Upload the reference images again before starting analysis.";
  return customerDetailsBlockReason("Complete Customer details before starting analysis")
    || quoteCompanyDetailsBlockReason("Complete Quote Company details before starting analysis");
}

function canStartAnalysis() {
  return !startAnalysisBlockReason();
}

function hasSubmittedQuoteBasis() {
  if (state.aiFailed) return false;
  const hasClarificationQuestions = (state.blockingClarificationQuestions || []).length > 0;
  return ["basis_review", "generating", "pricing_review", "completed"].includes(state.workflowStage)
    && (quoteOutputProgressForNavigation() || hasClarificationQuestions || state.lineItems.length > 0 || state.quoteBasisSections.some((section) => (section.lines || []).length > 0) || Object.values(state.quoteBasis).some((value) => splitLines(value).length > 0));
}

function hasCompletedQuoteBasis() {
  return state.basisConfirmed || quoteOutputProgressForNavigation() || ["pricing_review", "completed"].includes(state.workflowStage);
}

function applyDraftBasis(basis = {}) {
  state.basisConfirmed = false;
  const sections = confirmOnlyQuoteBasisSections(basis);
  state.quoteBasisSections = sections;
  const legacyBasis = basis.quote_basis && typeof basis.quote_basis === "object" ? basis.quote_basis : basis;
  state.quoteBasis = { ...cloneQuoteBasis(legacyBasis), ...quoteBasisFromSections(sections) };
}

function applyDraftLineItems(lineItems = []) {
  state.basisConfirmed = false;
  state.lineItems = lineItems.map(normalizeLineItem);
  state.outputRows = [];
  state.originalOutputRows = [];
  state.outputErrors = [];
}

async function refreshLineItemsFromServer() {
  if (!state.lineItems.length) return { ok: true, data: { status: "normalized", line_items: [] } };
  const result = await postJson("/api/line-items/normalize", buildLineItemNormalizePayload());
  if (!result.ok || !Array.isArray(result.data.line_items)) return result;
  state.lineItems = result.data.line_items.map(normalizeLineItem);
  state.outputRows = [];
  state.outputErrors = [];
  return result;
}

function captureOriginalAnalysisSnapshot(data = {}) {
  const sections = normalizeQuoteBasisSections(data.quote_basis_sections || data.quote_basis || state.quoteBasisSections);
  state.originalAnalysisSnapshot = {
    quote_basis_sections: cloneQuoteBasisSections(sections),
    quote_basis: { ...state.quoteBasis, ...quoteBasisFromSections(sections) },
    line_items: state.lineItems.map(normalizeLineItem),
    boothDimensions: { ...state.boothDimensions },
    source: data.source || state.draftSource || "",
    analysis_mode: normalizeAnalysisMode(data.analysis_mode || state.lastAnalysisMode),
    warnings: Array.isArray(data.warnings) ? [...data.warnings] : [],
  };
}

function openBlockingClarifications(questions = [], findings = [], project = {}) {
  state.analysisFindings = Array.isArray(findings) ? findings : [];
  state.blockingClarificationQuestions = Array.isArray(questions) ? questions : [];
  state.quoteBasis = { ...EMPTY_BASIS };
  state.quoteBasisSections = [];
  state.lineItems = [];
  state.outputRows = [];
  state.originalOutputRows = [];
  state.basisConfirmed = false;
  state.boothDimensions = normalizeBoothDimensions(project || state.boothDimensions);
  setDownloadFiles([]);
  setWorkflowStage("basis_review");
  renderClarificationQuestions();
  setSidePanel("basis", { force: true });
  syncControlStates();
}

function renderAnalysisFindings(findings = state.analysisFindings || []) {
  const visibleFindings = (Array.isArray(findings) ? findings : [])
    .filter((finding) => String(finding?.text || "").trim());
  if (!visibleFindings.length) return "";
  return `
    <div class="analysis-findings-card">
      <h4>Analysis Findings</h4>
      <ul>${visibleFindings.map((finding) => `<li>${escapeHtml(finding.text)}${finding.confidence_pct ? ` (${finding.confidence_pct}%)` : ""}</li>`).join("")}</ul>
    </div>
  `;
}

function renderClarificationQuestionText(text = "") {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  const colonIndex = normalized.indexOf(":");
  const hasCompactLead = colonIndex > 0 && colonIndex <= 72;
  const title = hasCompactLead ? normalized.slice(0, colonIndex).trim() : "";
  const body = hasCompactLead ? normalized.slice(colonIndex + 1).trim() : normalized;
  const points = body
    .split(/(?:;\s+|\.\s+)/)
    .map((point) => point.replace(/\.$/, "").trim())
    .filter(Boolean);
  const visiblePoints = points.length ? points : [body || normalized];
  return `
    ${title ? `<strong class="clarification-question-title">${escapeHtml(title)}</strong>` : ""}
    <ul class="clarification-question-points">
      ${visiblePoints.map((point) => `<li>${escapeHtml(point)}</li>`).join("")}
    </ul>
  `;
}

function clarificationAnswerPlaceholder(question = {}) {
  return question.answer_type === "number"
    ? "e.g. 2"
    : "e.g. Confirm quantity, size, or requirement";
}

function renderClarificationQuestions() {
  const questions = state.blockingClarificationQuestions || [];
  elements.basisReviewSurface.innerHTML = `
    <div class="assistant-card quote-basis-card clarification-card">
      <div class="quote-basis-header">
        <div>
          <div class="quote-basis-title-row"><h3>Clarification Questions</h3><span>Required before final Quote Basis</span></div>
          <p>These answers can affect wording, quantity, inclusion, material, or pricing. Final Quote Basis and Output stay locked until all are answered.</p>
        </div>
      </div>
      <form id="clarificationForm" class="clarification-form">
        ${questions.map((question, index) => `
          <label class="clarification-question">
            <div class="clarification-question-copy">${renderClarificationQuestionText(question.question)}</div>
            ${question.reason ? `<small>${escapeHtml(question.reason)}</small>` : ""}
            ${Array.isArray(question.choices) && question.choices.length ? `
              <select data-clarification-index="${index}">
                <option value="">Choose...</option>
                ${question.choices.map((choice) => `<option value="${escapeHtml(choice)}" ${question.answer === choice ? "selected" : ""}>${escapeHtml(choice)}</option>`).join("")}
              </select>
            ` : `<input data-clarification-index="${index}" value="${escapeHtml(question.answer || "")}" inputmode="${question.answer_type === "number" ? "decimal" : "text"}" placeholder="${escapeHtml(clarificationAnswerPlaceholder(question))}">`}
          </label>
        `).join("")}
        <button class="primary-button" type="submit">Generate final Quote Basis</button>
      </form>
    </div>
  `;
  const form = qs("#clarificationForm");
  form?.addEventListener("submit", handleClarificationSubmit);
}

async function handleClarificationSubmit(event) {
  event.preventDefault();
  const inputs = Array.from(event.currentTarget.querySelectorAll("[data-clarification-index]"));
  inputs.forEach((input) => {
    const index = Number(input.dataset.clarificationIndex);
    if (!Number.isInteger(index) || !state.blockingClarificationQuestions[index]) return;
    state.blockingClarificationQuestions[index].answer = input.value.trim();
    state.blockingClarificationQuestions[index].status = input.value.trim() ? "answered" : "open";
  });
  if ((state.blockingClarificationQuestions || []).some((question) => !String(question.answer || "").trim())) {
    renderClarificationQuestions();
    return;
  }
  state.pendingFeedback = "Use these clarification answers to generate the final Quote Basis.";
  await handleDraftBasis({ finalAfterClarifications: true });
  state.pendingFeedback = "";
}

function clearAiFailedDraftState() {
  state.quoteBasis = { ...EMPTY_BASIS };
  state.quoteBasisSections = [];
  state.lineItems = [];
  state.outputRows = [];
  state.originalOutputRows = [];
  state.outputErrors = [];
  state.originalAnalysisSnapshot = null;
  state.basisConfirmed = false;
  setDownloadFiles([]);
  renderMatchSummary({});
  renderPricingMatches([]);
  clearPricingReviewMessages();
  setResultStatus("No usable AI draft", "is-bad");
}

function showAiFailedDraftState(data = {}) {
  clearAiFailedDraftState();
  state.aiFailed = true;
  state.draftSource = data.source || "local";
  state.lastAnalysisMode = normalizeAnalysisMode(data.analysis_mode || state.lastAnalysisMode);
  const message = aiAnalysisFailureMessage(data);
  setWorkflowStage("basis_review");
  showAiFailureBanner(message);
  renderBasisFailureState(message);
  setSidePanel("basis", { force: true });
  syncControlStates();
}

function resetQuoteBasisToOriginal() {
  const snapshot = state.originalAnalysisSnapshot;
  if (!snapshot) return;
  state.quoteBasisSections = cloneQuoteBasisSections(snapshot.quote_basis_sections || snapshot.quote_basis || []);
  state.quoteBasis = cloneQuoteBasis(snapshot.quote_basis || quoteBasisFromSections(state.quoteBasisSections));
  state.lineItems = Array.isArray(snapshot.line_items) ? snapshot.line_items.map(normalizeLineItem) : [];
  state.boothDimensions = normalizeBoothDimensions(snapshot.boothDimensions || state.boothDimensions);
  state.lastAnalysisMode = normalizeAnalysisMode(snapshot.analysis_mode || state.lastAnalysisMode);
  state.outputRows = [];
  state.originalOutputRows = [];
  state.outputErrors = [];
  state.analysisFindings = [];
  state.blockingClarificationQuestions = [];
  state.basisConfirmed = false;
  setDownloadFiles([]);
  clearPricingReviewMessages();
  setWorkflowStage("basis_review");
  updateQuoteBasisCard(snapshot.source || "restored");
  syncControlStates();
}

async function resetOutputDraft() {
  if (appIsBusy() || !state.originalOutputRows.length) return;
  state.outputRows = snapshotOutputRows(state.originalOutputRows);
  state.lineItems = outputRowsToLineItems();
  state.outputErrors = [];
  markOutputRowsDirty();
  renderPricingMatches(state.outputRows);
  renderMatchSummary({ pricing_matches: state.outputRows });
  renderOutputValidationMessages(outputRowsValid().errors);
  setResultStatus("Output reset to confirmed basis", "is-warn");
  syncControlStates();
}

function postJsonFetchFailure(url) {
  const errorReference = newClientErrorReference();
  if (!state.isPageUnloading) {
    logClientEvent("client_error", fetchFailureLogDetails(url, { error_reference: errorReference }));
  }
  return {
    ok: false,
    data: {
      status: "failed",
      fetch_failed: true,
      page_unloading: state.isPageUnloading,
      message: "fetch_failed",
      error_reference: errorReference,
      errors: genericFailureMessages({ error_reference: errorReference }),
    },
    status: 0,
  };
}

async function fetchPostJsonResponse(url, payload) {
  const headers = { "Content-Type": "application/json" };
  if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
  return fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
}

async function jsonFromResponse(response) {
  try {
    return await response.json();
  } catch {
    return {
      status: "failed",
      errors: genericFailureMessages(),
    };
  }
}


async function applySessionData(data = {}) {
  const nextRecoveryScope = String(data.browser_recovery_scope || "").trim();
  if (!data.csrf_token || !nextRecoveryScope) return false;
  const previousRecoveryScope = currentBrowserRecoveryScope();
  if (previousRecoveryScope && previousRecoveryScope !== nextRecoveryScope) {
    state.isRecoveryScopeTransitioning = true;
    state.isBooting = true;
    state.csrfToken = "";
    clearQuoteSessionDraftSaveTimer();
    markPageUnloading();
    await purgeBrowserRecoveryState();
    window.location.reload();
    return false;
  }
  state.isRecoveryScopeTransitioning = false;
  state.csrfHeaderName = data.csrf_header || CSRF_HEADER_NAME;
  state.csrfToken = data.csrf_token;
  state.authRequired = Boolean(data.auth_required);
  state.authenticated = Boolean(data.authenticated);
  state.authUser = data.user && typeof data.user === "object" ? data.user : null;
  state.browserRecoveryScope = nextRecoveryScope;
  if (data.permissions && typeof data.permissions === "object") {
    state.permissions = { ...state.permissions, ...data.permissions };
  }
  renderAuthState();
  return true;
}

function authRoleLabel() {
  const role = String(state.permissions?.role || "").trim().toLowerCase();
  if (!role) return "";
  return role.replace(/(^|_)([a-z])/g, (_match, prefix, letter) => `${prefix ? " " : ""}${letter.toUpperCase()}`);
}

function renderAuthState() {
  if (!elements.topbarAuthState || !elements.topbarAuthText) return;
  const showAuthState = Boolean(state.authRequired);
  elements.topbarAuthState.hidden = !showAuthState;
  if (!showAuthState) return;
  if (state.authenticated) {
    const role = authRoleLabel();
    elements.topbarAuthText.textContent = role ? `Signed in as approved tester (${role})` : "Signed in as approved tester";
  } else {
    elements.topbarAuthText.textContent = "Sign in required for internal UAT";
  }
  if (elements.topbarLogoutLink) elements.topbarLogoutLink.hidden = !state.authenticated;
}

async function refreshSessionToken() {
  const { ok, data } = await getJson("/api/session", { logFetchFailure: false });
  return ok && await applySessionData(data);
}

async function postJson(url, payload) {
  let response;
  try {
    response = await fetchPostJsonResponse(url, payload);
  } catch (error) {
    return postJsonFetchFailure(url, error);
  }

  let data = await jsonFromResponse(response);
  if (response.status === 403 && await refreshSessionToken()) {
    try {
      response = await fetchPostJsonResponse(url, payload);
      data = await jsonFromResponse(response);
    } catch (error) {
      return postJsonFetchFailure(url, error);
    }
  }
  if (!response.ok) {
    logClientEvent("server_error", { url, status: response.status, errors: data.errors || [] });
  }
  return { ok: response.ok, data, status: response.status };
}

async function getJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    const errorReference = newClientErrorReference();
    if (!state.isPageUnloading && options.logFetchFailure !== false) {
      logClientEvent("client_error", fetchFailureLogDetails(url, { error_reference: errorReference }));
    }
    return {
      ok: false,
      data: {
        status: "failed",
        fetch_failed: true,
        page_unloading: state.isPageUnloading,
        message: "fetch_failed",
        error_reference: errorReference,
        errors: genericFailureMessages({ error_reference: errorReference }),
      },
    };
  }

  let data;
  try {
    data = await response.json();
  } catch {
    data = {
      status: "failed",
      errors: genericFailureMessages(),
    };
  }
  if (!response.ok) {
    logClientEvent("server_error", {
      url,
      status: response.status,
      error_reference: errorReferenceFrom(data),
      errors: data.errors || [],
    });
  }
  return { ok: response.ok, data };
}

function dashboardNumberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(String(value).replaceAll(",", "").trim());
  return Number.isFinite(number) ? number : null;
}

function dashboardTimestampMs(value = "") {
  const parsed = Date.parse(String(value || ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatDashboardDateTime(value = "") {
  const timestamp = dashboardTimestampMs(value);
  if (!timestamp) return "-";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function formatDashboardMoney(session = {}) {
  const commercials = session.commercials || {};
  const total = dashboardNumberOrNull(commercials.grand_total);
  if (total === null) return "-";
  const currency = String(commercials.currency || DEFAULT_CURRENCY_LABEL).trim() || DEFAULT_CURRENCY_LABEL;
  return `${currency} ${total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatDashboardSubtotal(session = {}) {
  const commercials = session.commercials || {};
  return formatDashboardMoneyValue(dashboardNumberOrNull(commercials.subtotal), dashboardSessionCurrency(session));
}

function dashboardGrandTotalValue(session = {}) {
  return dashboardNumberOrNull(session.commercials?.grand_total);
}

function dashboardSessionCurrency(session = {}) {
  return String(session.commercials?.currency || DEFAULT_CURRENCY_LABEL).trim() || DEFAULT_CURRENCY_LABEL;
}

function formatDashboardMoneyValue(total, currency = DEFAULT_CURRENCY_LABEL) {
  if (total === null || total === undefined || !Number.isFinite(Number(total))) return "-";
  const normalizedCurrency = String(currency || DEFAULT_CURRENCY_LABEL).trim() || DEFAULT_CURRENCY_LABEL;
  return `${normalizedCurrency} ${Number(total).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function dashboardCommercialsFromState() {
  const tax = collectTaxDetails();
  const stats = matchSummaryStats(state.outputRows);
  const hasConfirmedTotal = state.outputRows.length > 0 && !stats.totalPending;
  const subtotal = hasConfirmedTotal ? stats.total : null;
  const taxRate = Number(tax.rate ?? DEFAULT_TAX_RATE);
  const taxAmount = subtotal === null || !Number.isFinite(taxRate) ? null : subtotal * taxRate;
  return {
    currency: collectQuoteCurrency(),
    tax_label: tax.label || DEFAULT_TAX_LABEL,
    tax_rate: Number.isFinite(taxRate) ? taxRate : DEFAULT_TAX_RATE,
    exchange_rate: collectQuoteExchangeRate(),
    subtotal,
    tax_amount: taxAmount,
    grand_total: subtotal === null || taxAmount === null ? null : subtotal + taxAmount,
  };
}

function quoteDraftHasReferenceFiles() {
  return state.images.some((image) => (
    String(image?.data_url || image?.session_file_key || image?.name || "").trim()
  ));
}

function quoteDraftHasAiAnalysis() {
  const basisValues = Object.values(state.quoteBasis || {}).some((value) => String(value || "").trim());
  return Boolean(
    basisValues
    || state.quoteBasisSections.length
    || state.lineItems.length
    || state.analysisFindings.length
    || state.blockingClarificationQuestions.length
    || state.originalAnalysisSnapshot
    || state.basisConfirmed
    || state.aiFailed
    || String(state.draftSource || "").trim()
  );
}

function quoteDraftHasOutputState() {
  return Boolean(
    state.outputRows.length
    || state.originalOutputRows.length
    || revisionNumber(state.outputRevision, 0) > 0
    || state.downloadFile
    || state.pdfFile
  );
}

function quoteSessionDraftStateCanSave() {
  return Boolean(
    state.quoteSessionDraftSaveStarted
    && !state.isRecoveryScopeTransitioning,
  );
}

function quoteSessionDraftReachedCustomerStep() {
  return activeSidePanelIndex() >= SIDE_PANEL_SEQUENCE.indexOf("customer");
}

function markQuoteSessionDraftSaveStartedAfterCustomerStep() {
  if (state.quoteSessionDraftSaveStarted || !state.images.length || !quoteSessionDraftReachedCustomerStep()) {
    return false;
  }
  state.quoteSessionDraftSaveStarted = true;
  saveSessionState();
  return true;
}

function quoteDraftShouldPersistToDashboard() {
  return quoteSessionDraftStateCanSave() && (quoteDraftHasReferenceFiles() || quoteDraftHasAiAnalysis() || quoteDraftHasOutputState());
}

function currentQuoteSessionDraftState() {
  const snapshot = buildSessionSnapshot();
  return {
    version: snapshot.version,
    savedAt: snapshot.savedAt,
    activeAppView: "quote",
    quoteSessionDraftSaveStarted: snapshot.quoteSessionDraftSaveStarted,
    profileId: snapshot.profileId,
    pricingReferenceId: snapshot.pricingReferenceId,
    pricingReferenceSource: snapshot.pricingReferenceSource,
    selectedPresetValue: snapshot.selectedPresetValue,
    quoteCommercialTouched: snapshot.quoteCommercialTouched,
    images: snapshot.images,
    quoteDetails: snapshot.quoteDetails,
    workflowStage: snapshot.workflowStage,
    quoteBasis: snapshot.quoteBasis,
    quoteBasisSections: snapshot.quoteBasisSections,
    lineItems: snapshot.lineItems,
    outputRows: snapshot.outputRows,
    originalOutputRows: snapshot.originalOutputRows,
    outputErrors: snapshot.outputErrors,
    outputSortMode: snapshot.outputSortMode,
    analysisFindings: snapshot.analysisFindings,
    blockingClarificationQuestions: snapshot.blockingClarificationQuestions,
    boothDimensions: snapshot.boothDimensions,
    originalAnalysisSnapshot: snapshot.originalAnalysisSnapshot,
    basisConfirmed: snapshot.basisConfirmed,
    aiFailed: snapshot.aiFailed,
    draftSource: snapshot.draftSource,
    lastAnalysisMode: snapshot.lastAnalysisMode,
    activeSidePanel: furthestQuoteSessionSidePanel(snapshot),
    downloadFile: snapshot.downloadFile,
    pdfFile: snapshot.pdfFile,
    outputRevision: snapshot.outputRevision,
    downloadFileRevision: snapshot.downloadFileRevision,
    pdfFileRevision: snapshot.pdfFileRevision,
    pricingMatches: snapshot.pricingMatches,
  };
}

function quoteSessionDraftComparisonKey(draftState = currentQuoteSessionDraftState()) {
  const comparable = JSON.parse(JSON.stringify(draftState || {}));
  delete comparable.savedAt;
  delete comparable.activeAppView;
  delete comparable.activeSidePanel;
  return JSON.stringify(comparable);
}

function rememberRestoredQuoteSessionBaseline(sessionId = state.quoteSessionId) {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  state.quoteSessionRestoredSessionId = safeSessionId;
  state.quoteSessionRestoredDraftKey = safeSessionId ? quoteSessionDraftComparisonKey() : "";
  saveSessionState();
}

function clearRestoredQuoteSessionBaseline() {
  state.quoteSessionRestoredSessionId = "";
  state.quoteSessionRestoredDraftKey = "";
}

function currentQuoteSessionIsRestoredFromDashboard() {
  const restoredSessionId = safeQuoteSessionId(state.quoteSessionRestoredSessionId || "");
  const currentSessionId = safeQuoteSessionId(state.quoteSessionId || "");
  return Boolean(restoredSessionId && currentSessionId && restoredSessionId === currentSessionId);
}

function restoredQuoteSessionHasChanged() {
  if (!currentQuoteSessionIsRestoredFromDashboard()) return true;
  const baseline = String(state.quoteSessionRestoredDraftKey || "");
  if (!baseline) return true;
  return quoteSessionDraftComparisonKey() !== baseline;
}

function mergeDashboardQuoteSession(session = {}) {
  const safeSessionId = safeQuoteSessionId(session.session_id || "");
  if (!safeSessionId) return;
  const existing = Array.isArray(state.quoteSessions) ? state.quoteSessions : [];
  const nextSession = { ...session, session_id: safeSessionId };
  const index = existing.findIndex((item) => safeQuoteSessionId(item.session_id || "") === safeSessionId);
  state.quoteSessions = index >= 0
    ? existing.map((item, itemIndex) => (itemIndex === index ? { ...item, ...nextSession } : item))
    : [nextSession, ...existing];
}

function currentQuoteSessionPayload(options = {}) {
  const details = collectQuoteDetails();
  const selectedProfile = selectedPreset();
  const selectedReference = currentPricingReference();
  const profile = currentProfile();
  const quoteGenerated = Boolean(options.quoteGenerated ?? quoteSessionHasFreshOutputExports()) && quoteSessionHasFreshOutputExports();
  const payload = {
    session_id: safeQuoteSessionId(options.sessionId || state.quoteSessionId || ""),
    customer_summary: {
      customer_name: details.client?.name || "",
      project_name: details.project?.title || "",
      show_name: details.project?.show_name || "",
      project_number: details.project_number || "",
      event_or_project_date: details.quote_date || "",
    },
    quote_company_profile: {
      id: safeProfileId(generationProfileIdForPayload(), ""),
      display_name: selectedProfile?.name || profile?.label || details.company?.name || "",
    },
    pricing_reference: {
      id: selectedReference?.id || state.pricingReferenceId || "",
      display_name: selectedReference?.label || "",
    },
    commercials: dashboardCommercialsFromState(),
    status: {
      quote_generated: quoteGenerated,
    },
  };
  if (options.includeDraftState === true && quoteSessionDraftStateCanSave()) {
    payload.draft_state = currentQuoteSessionDraftState();
    payload.draft_files = sessionFileRecordsFromDraft();
  }
  return payload;
}

async function saveCurrentQuoteSession(options = {}) {
  let requestedSessionId = safeQuoteSessionId(options.sessionId || state.quoteSessionId || "");
  if (!requestedSessionId && quoteSessionInitialSavePromise) {
    await quoteSessionInitialSavePromise.catch(() => null);
    requestedSessionId = safeQuoteSessionId(options.sessionId || state.quoteSessionId || "");
  }
  const isInitialSave = !requestedSessionId;
  if (isInitialSave) {
    requestedSessionId = ensureClientQuoteSessionId();
  }
  const payload = currentQuoteSessionPayload({ ...options, sessionId: requestedSessionId });
  const savePromise = (async () => {
    const { ok, data } = await postJson("/api/quote-sessions", payload);
    if (!ok) {
      state.quoteSessionLoadError = genericFailureMessage(data);
      return null;
    }
    const session = data.quote_session || {};
    state.quoteSessionId = safeQuoteSessionId(session.session_id || payload.session_id);
    mergeDashboardQuoteSession(session);
    if (currentQuoteSessionIsRestoredFromDashboard()) {
      state.quoteSessionRestoredDraftKey = quoteSessionDraftComparisonKey();
    }
    return session;
  })();
  if (isInitialSave) {
    quoteSessionInitialSavePromise = savePromise;
    savePromise.finally(() => {
      if (quoteSessionInitialSavePromise === savePromise) quoteSessionInitialSavePromise = null;
    }).catch(() => {});
  }
  return savePromise;
}

async function ensureQuoteSession(options = {}) {
  if (safeQuoteSessionId(state.quoteSessionId)) return state.quoteSessionId;
  const session = await saveCurrentQuoteSession(options);
  return safeQuoteSessionId(session?.session_id || state.quoteSessionId);
}

async function saveQuoteSessionDraftState(options = {}) {
  if (!quoteSessionDraftStateCanSave()) return null;
  saveSessionState();
  const session = await saveCurrentQuoteSession({
    quoteGenerated: Boolean(options.quoteGenerated ?? (state.basisConfirmed || state.outputRows.length)),
    includeDraftState: true,
  });
  if (session) saveSessionState();
  return session;
}

function clearQuoteSessionDraftSaveTimer() {
  if (!quoteSessionDraftSaveTimer) return;
  window.clearTimeout(quoteSessionDraftSaveTimer);
  quoteSessionDraftSaveTimer = null;
}

function queueQuoteSessionDraftStateSave(options = {}) {
  if (!quoteSessionDraftStateCanSave()) return;
  saveSessionState();
  clearQuoteSessionDraftSaveTimer();
  const delay = Number.isFinite(Number(options.delay)) ? Math.max(0, Number(options.delay)) : 650;
  quoteSessionDraftSaveTimer = window.setTimeout(() => {
    quoteSessionDraftSaveTimer = null;
    saveQuoteSessionDraftState(options).catch(() => {});
  }, delay);
}

function quoteCommercialFieldChanged(target = null) {
  return [
    elements.taxLabel,
    elements.taxRate,
    elements.quoteCurrency,
    elements.quoteCurrencyCustom,
    elements.quoteTaxLabel,
    elements.quoteTaxRate,
    elements.quoteExchangeRate,
  ].filter(Boolean).includes(target);
}

function handleQuoteDetailFieldChange(event = {}) {
  const commercialChanged = quoteCommercialFieldChanged(event.target);
  if (event.target === elements.quoteCurrency) {
    syncQuoteCurrencyCustomInput();
  }
  if (event.target === elements.quoteCurrencyCustom) {
    const normalizedValue = normalizedCustomCurrencyInput(elements.quoteCurrencyCustom);
    if (elements.quoteCurrencyCustom.value !== normalizedValue) {
      elements.quoteCurrencyCustom.value = normalizedValue;
    }
  }
  markQuoteCommercialFieldTouched(event.target);
  syncQuoteExchangeRateField();
  if (commercialChanged && (state.outputRows.length || state.downloadFile || state.pdfFile)) {
    markOutputRowsDirty();
  }
  updateOutputHeader();
  syncControlStates();
  if (quoteSessionDraftStateCanSave()) {
    queueQuoteSessionDraftStateSave({ quoteGenerated: Boolean(state.basisConfirmed || state.outputRows.length) });
  }
}

async function startQuoteSessionDraftSaveAfterCustomerStep() {
  if (!markQuoteSessionDraftSaveStartedAfterCustomerStep()) return;
  await saveQuoteSessionDraftState({ quoteGenerated: false });
}

async function saveQuoteSessionDraftStateAfterPanelMove(panelName = state.activeSidePanel) {
  const panelIndex = SIDE_PANEL_SEQUENCE.indexOf(panelName);
  if (panelIndex < SIDE_PANEL_SEQUENCE.indexOf("customer")) return;
  if (!state.quoteSessionDraftSaveStarted) {
    await startQuoteSessionDraftSaveAfterCustomerStep();
    return;
  }
  await saveQuoteSessionDraftState({ quoteGenerated: Boolean(state.basisConfirmed || state.outputRows.length) });
}

function hasCurrentQuoteDraft() {
  return quoteDraftShouldPersistToDashboard();
}

async function deleteQuoteSessionRecord(sessionId = "") {
  const safeSessionId = safeQuoteSessionId(sessionId);
  if (!safeSessionId) return true;
  const headers = {};
  if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
  try {
    const response = await fetch(`/api/quote-sessions/${encodeURIComponent(safeSessionId)}`, {
      method: "DELETE",
      headers,
    });
    return response.ok || response.status === 404;
  } catch (error) {
    const errorReference = newClientErrorReference();
    logClientEvent("client_error", fetchFailureLogDetails(`/api/quote-sessions/${safeSessionId}`, { error_reference: errorReference }));
    return false;
  }
}

async function discardCurrentQuoteDraftSession() {
  await deleteQuoteSessionRecord(state.quoteSessionId);
  clearSessionState();
  resetCurrentQuoteDraftState();
}

function setDashboardLoadingState(isLoading = false, options = {}) {
  state.quoteSessionDashboardBusy = Boolean(isLoading);
  if (elements.dashboardLoadingModal) elements.dashboardLoadingModal.hidden = !state.quoteSessionDashboardBusy;
  if (elements.dashboardLoadingTitle) elements.dashboardLoadingTitle.textContent = options.title || "Opening dashboard";
  if (elements.dashboardLoadingMessage) elements.dashboardLoadingMessage.textContent = options.message || "Saving the current quote and refreshing the session list.";
  syncControlStates();
}

function showQuoteFlow() {
  state.activeAppView = "quote";
  elements.quoteDashboardPanel?.classList.remove("is-active");
  elements.quoteFlowPanel?.classList.add("is-active");
  if (elements.backToDashboardButton) elements.backToDashboardButton.hidden = false;
  if (elements.newQuoteButton) elements.newQuoteButton.hidden = true;
  syncControlStates();
}

function showDashboard(options = {}) {
  state.activeAppView = "dashboard";
  elements.quoteFlowPanel?.classList.remove("is-active");
  elements.quoteDashboardPanel?.classList.add("is-active");
  if (elements.backToDashboardButton) elements.backToDashboardButton.hidden = true;
  if (elements.newQuoteButton) elements.newQuoteButton.hidden = false;
  syncControlStates();
  if (options.load !== false) {
    loadQuoteDashboard({ showLoading: options.showLoading !== false });
  }
}

async function returnToDashboard() {
  if (appIsBusy()) return;
  let dashboardLoadStarted = false;
  setDashboardLoadingState(true, {
    title: "Opening dashboard",
    message: "Saving the current quote and refreshing the session list.",
  });
  try {
    clearQuoteSessionDraftSaveTimer();
    markQuoteSessionDraftSaveStartedAfterCustomerStep();
    if (currentQuoteSessionIsRestoredFromDashboard() && !restoredQuoteSessionHasChanged()) {
      dashboardLoadStarted = true;
      showDashboard();
      return;
    }
    if (!quoteDraftShouldPersistToDashboard()) {
      if (currentQuoteSessionIsRestoredFromDashboard()) {
        dashboardLoadStarted = true;
        showDashboard();
        return;
      }
      await discardCurrentQuoteDraftSession();
      dashboardLoadStarted = true;
      showDashboard();
      return;
    }
    await saveCurrentQuoteSession({
      quoteGenerated: Boolean(state.basisConfirmed || state.outputRows.length),
      includeDraftState: true,
    });
    dashboardLoadStarted = true;
    showDashboard();
  } finally {
    if (!dashboardLoadStarted) setDashboardLoadingState(false);
  }
}

async function handleTopbarBrandClick() {
  if (appIsBusy()) return;
  if (state.activeAppView === "dashboard") {
    elements.quoteDashboardPanel?.scrollTo?.({ top: 0, behavior: "smooth" });
    window.scrollTo?.({ top: 0, behavior: "smooth" });
    return;
  }
  await returnToDashboard();
}

async function loadQuoteDashboard(options = {}) {
  if (!elements.quoteDashboardPanel) return;
  const loadId = (Number(state.quoteSessionDashboardLoadId) || 0) + 1;
  state.quoteSessionDashboardLoadId = loadId;
  const showLoading = options.showLoading !== false;
  const preservedView = options.preserveViewState === true ? {
    selectionMode: state.dashboardSelectionMode,
    selectedSessionIds: dashboardSelectedSessionIds(),
    activeSessionId: safeQuoteSessionId(state.dashboardActiveSessionId || ""),
  } : null;
  if (showLoading) {
    setDashboardLoadingState(true, {
      title: "Loading quote sessions",
      message: "Refreshing the dashboard list and saved exports.",
    });
  }
  state.quoteSessionLoadError = "";
  state.quoteSessions = [];
  state.dashboardActiveSessionId = "";
  state.dashboardSelectedSessionIds = [];
  renderQuoteDashboard();
  try {
    const { ok, data } = await getJson("/api/quote-sessions", { logFetchFailure: false });
    if (state.quoteSessionDashboardLoadId !== loadId) return;
    if (!ok) {
      state.quoteSessions = [];
      state.quoteSessionLoadError = genericFailureMessage(data);
    } else {
      state.quoteSessions = Array.isArray(data.quote_sessions) ? data.quote_sessions : [];
    }
    if (preservedView) {
      const availableIds = new Set(state.quoteSessions.map((session) => safeQuoteSessionId(session.session_id || "")).filter(Boolean));
      state.dashboardSelectedSessionIds = preservedView.selectedSessionIds.filter((sessionId) => availableIds.has(sessionId));
      state.dashboardActiveSessionId = availableIds.has(preservedView.activeSessionId) ? preservedView.activeSessionId : "";
      state.dashboardSelectionMode = Boolean(preservedView.selectionMode && state.dashboardSelectedSessionIds.length);
    }
    renderQuoteDashboard();
  } finally {
    if (state.quoteSessionDashboardLoadId === loadId && showLoading) setDashboardLoadingState(false);
  }
}

function quoteSessionExport(session = {}, kind = "xlsx") {
  const exports = session.exports && typeof session.exports === "object" ? session.exports : {};
  const item = exports[kind] && typeof exports[kind] === "object" ? exports[kind] : {};
  return item;
}

function quoteSessionHasMissingExport(session = {}) {
  return ["xlsx", "pdf"].some((kind) => Boolean(quoteSessionExport(session, kind).missing));
}

function quoteSessionHasAvailableExport(session = {}) {
  return ["xlsx", "pdf"].some((kind) => Boolean(quoteSessionExport(session, kind).exists));
}

function quoteSessionHasStaleExport(session = {}) {
  return ["xlsx", "pdf"].some((kind) => Boolean(quoteSessionExport(session, kind).stale));
}

function quoteSessionStatus(session = {}) {
  const hasAvailableExport = quoteSessionHasAvailableExport(session);
  const hasStaleExport = quoteSessionHasStaleExport(session);
  if (hasAvailableExport) return { key: "generated", label: "Generated", className: "is-generated" };
  if (session.status?.draft_modified || hasStaleExport) return { key: "draft-modified", label: "Draft Modified", className: "is-draft-modified" };
  if (session.status?.quote_generated) return { key: "generated", label: "Generated", className: "is-generated" };
  return { key: "draft", label: "Draft", className: "is-draft" };
}

function dashboardSessionProgressLabel(session = {}) {
  const progress = session.draft_progress && typeof session.draft_progress === "object" ? session.draft_progress : {};
  const label = String(progress.label || "").trim();
  return label ? `Saved at ${label}` : "";
}

function dashboardProgressStageClass(label = "") {
  const normalized = String(label || "")
    .replace(/^Saved at\s+/i, "")
    .trim()
    .toLowerCase();
  if (normalized === "upload") return "is-progress-upload";
  if (normalized === "customer") return "is-progress-customer";
  if (normalized === "quote company") return "is-progress-quote-company";
  if (normalized === "quote basis") return "is-progress-quote-basis";
  if (normalized === "output") return "is-progress-output";
  return "is-progress-generic";
}

function dashboardSessionProgressPill(session = {}) {
  const label = dashboardSessionProgressLabel(session);
  const stageClass = dashboardProgressStageClass(label);
  return label ? `<span class="dashboard-status-pill dashboard-progress-pill is-progress ${escapeHtml(stageClass)}" aria-label="Latest saved workflow step: ${escapeHtml(label)}">${escapeHtml(label)}</span>` : "";
}

function dashboardSessionCustomerText(session = {}) {
  return String(session.customer_summary?.customer_name || "").trim() || "Untitled customer";
}

function dashboardSessionProjectText(session = {}) {
  return String(session.customer_summary?.project_name || "").trim() || "Untitled quote";
}

function dashboardSessionShowNameText(session = {}) {
  return String(session.customer_summary?.show_name || "").trim();
}

function dashboardSessionProjectNumberText(session = {}) {
  return String(session.customer_summary?.project_number || "").trim();
}

function dashboardSessionSearchText(session = {}) {
  const shortReference = dashboardShortSessionReference(session);
  return [
    shortReference,
    shortReference ? `ref ${shortReference}` : "",
    dashboardSessionCustomerText(session),
    dashboardSessionProjectText(session),
    dashboardSessionShowNameText(session),
    dashboardSessionProjectNumberText(session),
  ].map((value) => String(value || "").toLowerCase()).join(" ");
}

function dashboardDateFilterMatches(session = {}, filter = "all") {
  const normalizedFilter = String(filter || "all");
  if (normalizedFilter === "all") return true;
  const timestamp = dashboardTimestampMs(session.updated_at || session.created_at);
  if (!timestamp) return false;
  if (normalizedFilter === "custom") {
    const start = dashboardDateInputMs(state.dashboardCustomDateStart);
    const end = dashboardDateInputMs(state.dashboardCustomDateEnd, { endOfDay: true });
    if (!start && !end) return true;
    const lower = start && end ? Math.min(start, end) : start;
    const upper = start && end ? Math.max(start, end) : end;
    if (lower && timestamp < lower) return false;
    if (upper && timestamp > upper) return false;
    return true;
  }
  const now = Date.now();
  if (normalizedFilter === "today") {
    const sessionDate = new Date(timestamp);
    const today = new Date(now);
    return sessionDate.getFullYear() === today.getFullYear()
      && sessionDate.getMonth() === today.getMonth()
      && sessionDate.getDate() === today.getDate();
  }
  const days = normalizedFilter === "7d" ? 7 : normalizedFilter === "30d" ? 30 : 0;
  if (!days) return true;
  return timestamp >= now - days * 24 * 60 * 60 * 1000;
}

function dashboardDateInputMs(value = "", options = {}) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || "").trim());
  if (!match) return 0;
  const year = Number(match[1]);
  const month = Number(match[2]) - 1;
  const day = Number(match[3]);
  const date = new Date(
    year,
    month,
    day,
    options.endOfDay ? 23 : 0,
    options.endOfDay ? 59 : 0,
    options.endOfDay ? 59 : 0,
    options.endOfDay ? 999 : 0
  );
  if (date.getFullYear() !== year || date.getMonth() !== month || date.getDate() !== day) return 0;
  return date.getTime();
}

function dashboardDateInputValueFromMs(timestamp = 0) {
  const date = new Date(Number(timestamp) || Date.now());
  if (Number.isNaN(date.getTime())) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function dashboardEarliestSessionDateInput(sessions = state.quoteSessions || []) {
  const timestamps = sessions
    .map((session) => dashboardTimestampMs(session.updated_at || session.created_at))
    .filter(Boolean);
  if (!timestamps.length) return dashboardDateInputValueFromMs(Date.now());
  return dashboardDateInputValueFromMs(Math.min(...timestamps));
}

function ensureDashboardCustomDateDefaults() {
  if ((state.dashboardDateFilter || "all") !== "custom") return;
  if (!state.dashboardCustomDateStart) {
    state.dashboardCustomDateStart = dashboardEarliestSessionDateInput();
  }
  if (!state.dashboardCustomDateEnd) {
    state.dashboardCustomDateEnd = dashboardDateInputValueFromMs(Date.now());
  }
}

function dashboardSortValue(session = {}, mode = state.dashboardSortMode || "created") {
  const normalizedMode = String(mode || "created");
  const primary = normalizedMode === "modified" ? session.updated_at || session.created_at : session.created_at;
  return dashboardTimestampMs(primary);
}

function filteredDashboardSessions() {
  const statusFilter = state.dashboardStatusFilter || "all";
  const dateFilter = state.dashboardDateFilter || "all";
  const search = String(state.dashboardSearch || "").trim().toLowerCase();
  const sortMode = state.dashboardSortMode || "created";
  return state.quoteSessions.map((session, index) => ({ session, index })).filter(({ session }) => {
    const status = quoteSessionStatus(session).key;
    if (statusFilter !== "all" && status !== statusFilter) return false;
    if (!dashboardDateFilterMatches(session, dateFilter)) return false;
    if (search && !dashboardSessionSearchText(session).includes(search)) return false;
    return true;
  }).sort((left, right) => {
    const primaryDelta = dashboardSortValue(right.session, sortMode) - dashboardSortValue(left.session, sortMode);
    if (primaryDelta) return primaryDelta;
    const modifiedDelta = dashboardTimestampMs(right.session.updated_at || right.session.created_at) - dashboardTimestampMs(left.session.updated_at || left.session.created_at);
    if (modifiedDelta) return modifiedDelta;
    return left.index - right.index;
  }).map(({ session }) => session);
}

function dashboardPageSizeValue() {
  const size = Number(state.dashboardPageSize);
  return DASHBOARD_PAGE_SIZE_OPTIONS.includes(size) ? size : DASHBOARD_DEFAULT_PAGE_SIZE;
}

function dashboardPageCount(total, pageSize = dashboardPageSizeValue()) {
  if (!Number.isFinite(total) || total <= 0) return 1;
  if (pageSize <= 0) return 1;
  return Math.max(1, Math.ceil(total / pageSize));
}

function clampDashboardPageIndex(total) {
  const maxIndex = dashboardPageCount(total) - 1;
  const index = Number(state.dashboardPageIndex);
  state.dashboardPageIndex = Math.min(Math.max(Number.isFinite(index) ? Math.floor(index) : 0, 0), maxIndex);
  return state.dashboardPageIndex;
}

function dashboardPageRange(total) {
  const pageSize = dashboardPageSizeValue();
  const pageIndex = clampDashboardPageIndex(total);
  if (!Number.isFinite(total) || total <= 0) {
    return { start: 0, end: 0, pageIndex, pageSize };
  }
  if (pageSize <= 0) {
    return { start: 1, end: total, pageIndex: 0, pageSize };
  }
  const start = pageIndex * pageSize + 1;
  const end = Math.min(total, start + pageSize - 1);
  return { start, end, pageIndex, pageSize };
}

function pagedDashboardSessions(filtered = filteredDashboardSessions()) {
  const sessions = Array.isArray(filtered) ? filtered : [];
  const { start, end, pageSize } = dashboardPageRange(sessions.length);
  if (pageSize <= 0 || sessions.length <= 0) return sessions;
  return sessions.slice(start - 1, end);
}

function renderDashboardPageControls(filtered = []) {
  const sessions = Array.isArray(filtered) ? filtered : [];
  const total = sessions.length;
  const pageSize = dashboardPageSizeValue();
  const hasStoredSessions = state.quoteSessions.length > 0 && !state.quoteSessionLoadError;
  if (elements.dashboardPageControls) elements.dashboardPageControls.hidden = !hasStoredSessions;
  if (elements.dashboardPageSizeSelect) {
    elements.dashboardPageSizeSelect.value = String(pageSize);
    elements.dashboardPageSizeSelect.disabled = !hasStoredSessions;
  }
  if (!elements.dashboardRangeSelect) return;
  const pageCount = dashboardPageCount(total, pageSize);
  const activePageIndex = clampDashboardPageIndex(total);
  const options = [];
  for (let index = 0; index < pageCount; index += 1) {
    const start = pageSize <= 0 ? 1 : index * pageSize + 1;
    const end = pageSize <= 0 ? total : Math.min(total, start + pageSize - 1);
    const label = total > 0 ? `${start}-${end}` : "0-0";
    options.push(`<option value="${index}">${label}</option>`);
  }
  elements.dashboardRangeSelect.innerHTML = options.join("");
  elements.dashboardRangeSelect.value = String(activePageIndex);
  elements.dashboardRangeSelect.disabled = !hasStoredSessions;
}

function updateDashboardSummary() {
  const sessions = state.quoteSessions;
  const generated = sessions.filter((session) => quoteSessionStatus(session).key === "generated").length;
  const exported = sessions.filter(quoteSessionHasAvailableExport).length;
  if (elements.dashboardTotalSessions) elements.dashboardTotalSessions.textContent = String(sessions.length);
  if (elements.dashboardGeneratedSessions) elements.dashboardGeneratedSessions.textContent = String(generated);
  if (elements.dashboardExportedSessions) elements.dashboardExportedSessions.textContent = String(exported);
}

function dashboardModifiedText(session = {}) {
  return formatDashboardDateTime(session.updated_at || session.created_at);
}

function dashboardTaxText(session = {}) {
  const commercials = session.commercials || {};
  const label = String(commercials.tax_label || DEFAULT_TAX_LABEL).trim() || DEFAULT_TAX_LABEL;
  return label;
}

function dashboardTaxRateText(session = {}) {
  const commercials = session.commercials || {};
  const rate = Number(commercials.tax_rate ?? DEFAULT_TAX_RATE);
  return Number.isFinite(rate) ? `${(rate * 100).toLocaleString(undefined, { maximumFractionDigits: 2 })}%` : "-";
}

const QUOTE_SESSION_DELETE_SINGLE_MESSAGE = "This removes the local dashboard record and any saved local exports for this quote session. This cannot be undone.";
const QUOTE_SESSION_DELETE_BULK_MESSAGE = "This removes the selected local dashboard records and any saved local exports for those quote sessions. This cannot be undone.";

function dashboardShortSessionReference(session = {}) {
  const sessionId = safeQuoteSessionId(session.session_id || "");
  return sessionId ? sessionId.slice(0, 8).toUpperCase() : "";
}



function dashboardSessionCanResume(session = {}) {
  const sessionId = safeQuoteSessionId(session.session_id || "");
  return Boolean(
    sessionId
    && sessionId === safeQuoteSessionId(state.quoteSessionId)
    && hasCurrentQuoteDraft()
    && !quoteSessionHasAvailableExport(session)
    && !quoteSessionHasMissingExport(session)
  );
}

function dashboardSessionHasCurrentDraft(session = {}) {
  const sessionId = safeQuoteSessionId(session.session_id || "");
  return Boolean(sessionId && sessionId === safeQuoteSessionId(state.quoteSessionId) && quoteDraftShouldPersistToDashboard());
}

function dashboardDraftImageFileFieldsMatch(candidate = {}, image = {}, options = {}) {
  const candidateName = String(candidate?.name || "").trim().toLowerCase();
  const imageName = String(image?.name || "").trim().toLowerCase();
  if (!candidateName || !imageName || candidateName !== imageName) return false;
  const candidateType = referenceFileType(candidate);
  const imageType = referenceFileType(image);
  const candidateSize = Number(candidate?.size);
  const imageSize = Number(image?.size);
  const requireTypeAndSize = Boolean(options.requireTypeAndSize);
  const typeMatches = requireTypeAndSize
    ? Boolean(candidateType && imageType && candidateType === imageType)
    : (!candidateType || !imageType || candidateType === imageType);
  const sizeMatches = requireTypeAndSize
    ? (Number.isFinite(candidateSize) && Number.isFinite(imageSize) && candidateSize === imageSize)
    : (!Number.isFinite(candidateSize) || !Number.isFinite(imageSize) || candidateSize === imageSize);
  return typeMatches && sizeMatches;
}

function dashboardDraftImagePayloadMatches(candidate = {}, image = {}) {
  const candidateKey = String(candidate?.session_file_key || "").trim();
  const imageKey = String(image?.session_file_key || "").trim();
  if (candidateKey && imageKey) {
    return candidateKey === imageKey || dashboardDraftImageFileFieldsMatch(candidate, image, { requireTypeAndSize: true });
  }
  return dashboardDraftImageFileFieldsMatch(candidate, image);
}

function dashboardDraftLogoSessionFileKey(draftState = {}) {
  const quoteDetails = draftState?.quoteDetails && typeof draftState.quoteDetails === "object" ? draftState.quoteDetails : {};
  const company = quoteDetails.company && typeof quoteDetails.company === "object" ? quoteDetails.company : {};
  return String(company.logo_session_file_key || "").trim();
}

function dashboardDraftPayloadIsReferenceFile(record = {}, draftState = {}) {
  const role = String(record?.file_role || record?.role || "").trim().toLowerCase();
  if (["quote_company_logo", "header_logo", "logo"].includes(role)) return false;
  const recordKey = String(record?.session_file_key || "").trim();
  const logoKey = dashboardDraftLogoSessionFileKey(draftState);
  return !(recordKey && logoKey && recordKey === logoKey);
}

function mergeDashboardDraftImagesWithAvailablePayloads(draftState = {}, availableImages = []) {
  const draftImages = Array.isArray(draftState?.images) ? draftState.images.slice(0, MAX_REFERENCE_IMAGES) : [];
  const payloadImages = (Array.isArray(availableImages) ? availableImages : [])
    .filter((image) => String(image?.data_url || "").trim() && dashboardDraftPayloadIsReferenceFile(image, draftState))
    .slice(0, MAX_REFERENCE_IMAGES)
    .map((image) => ({
      ...image,
      type: referenceFileType(image),
    }));
  if (!payloadImages.length) return draftState;
  if (!draftImages.length) {
    return {
      ...draftState,
      images: payloadImages.slice(0, MAX_REFERENCE_IMAGES).map((payload) => ({
        ...payload,
        type: payload.type || referenceFileType(payload),
      })),
    };
  }
  const usedPayloadIndexes = new Set();
  const mergedImages = draftImages.map((image, index) => {
    if (String(image?.data_url || "").trim()) {
      const payloadIndex = payloadImages.findIndex((candidate, candidateIndex) => (
        !usedPayloadIndexes.has(candidateIndex)
        && dashboardDraftImagePayloadMatches(candidate, image)
      ));
      if (payloadIndex >= 0) usedPayloadIndexes.add(payloadIndex);
      return image;
    }
    let payloadIndex = payloadImages.findIndex((candidate, candidateIndex) => (
      !usedPayloadIndexes.has(candidateIndex)
      && dashboardDraftImagePayloadMatches(candidate, image)
    ));
    if (payloadIndex < 0 && payloadImages.length > 0 && !usedPayloadIndexes.has(index)) {
      payloadIndex = payloadImages.findIndex((_, candidateIndex) => !usedPayloadIndexes.has(candidateIndex));
    }
    const payloadImage = payloadIndex >= 0 ? payloadImages[payloadIndex] : null;
    const dataUrl = String(payloadImage?.data_url || "").trim();
    if (!dataUrl) return image;
    usedPayloadIndexes.add(payloadIndex);
    const size = Number.isFinite(Number(image?.size)) ? Number(image.size) : Number(payloadImage?.size);
    return {
      ...image,
      data_url: dataUrl,
      type: image?.type || payloadImage?.type || referenceFileType(payloadImage),
      size: Number.isFinite(size) ? size : 0,
      session_file_key: image?.session_file_key || payloadImage?.session_file_key || "",
    };
  });
  const remainingPayloads = payloadImages.filter((_, index) => !usedPayloadIndexes.has(index))
    .slice(0, MAX_REFERENCE_IMAGES - mergedImages.length);
  return {
    ...draftState,
    images: [
      ...mergedImages,
      ...remainingPayloads.map((payloadImage) => ({
        ...payloadImage,
        type: payloadImage.type || referenceFileType(payloadImage),
      })),
    ].slice(0, MAX_REFERENCE_IMAGES),
  };
}
function hydrateDashboardDraftImagePayloads(draftState = {}, sessionId = "") {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  if (!safeSessionId || safeSessionId !== safeQuoteSessionId(state.quoteSessionId || "")) return draftState;
  return mergeDashboardDraftImagesWithAvailablePayloads(draftState, state.images);
}

function mergeDashboardDraftSummaryDetails(draftState = {}, session = {}) {
  const summary = session?.customer_summary && typeof session.customer_summary === "object"
    ? session.customer_summary
    : {};
  const showName = String(summary.show_name || "").trim();
  const projectNumber = String(summary.project_number || "").trim();
  if (!showName && !projectNumber) return draftState;

  const quoteDetails = draftState?.quoteDetails && typeof draftState.quoteDetails === "object"
    ? draftState.quoteDetails
    : {};
  const project = quoteDetails.project && typeof quoteDetails.project === "object" ? quoteDetails.project : {};
  const nextProject = { ...project };
  if (showName && !String(nextProject.show_name || "").trim()) nextProject.show_name = showName;

  const nextQuoteDetails = { ...quoteDetails, project: nextProject };
  if (projectNumber && !String(nextQuoteDetails.project_number || "").trim()) {
    nextQuoteDetails.project_number = projectNumber;
  }
  return { ...draftState, quoteDetails: nextQuoteDetails };
}

function dashboardSessionCanModify(session = {}) {
  return Boolean(safeQuoteSessionId(session.session_id || ""));
}

async function loadQuoteSessionDetail(sessionId = "") {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  if (!safeSessionId) return null;
  const { ok, data } = await getJson(`/api/quote-sessions/${encodeURIComponent(safeSessionId)}`);
  if (!ok) {
    state.quoteSessionLoadError = genericFailureMessage(data);
    renderQuoteDashboard();
    return null;
  }
  const session = data.quote_session && typeof data.quote_session === "object" ? data.quote_session : null;
  return session && safeQuoteSessionId(session.session_id || "") === safeSessionId ? session : null;
}

function dashboardRestoreError(message) {
  state.quoteSessionRestoreError = message || "This quote session does not have saved draft data to modify.";
  renderQuoteDashboard();
}

function normalizeDashboardOperation(operation = {}) {
  if (!operation || typeof operation !== "object") return null;
  const type = operation.type === "modify" || operation.type === "duplicate" ? operation.type : "";
  const sourceSessionId = safeQuoteSessionId(operation.sourceSessionId || "");
  const targetSessionId = type === "duplicate" ? safeQuoteSessionId(operation.targetSessionId || "") : "";
  const browserRecoveryScope = String(operation.browserRecoveryScope || "").trim();
  const startedAt = String(operation.startedAt || "").trim();
  const startedMs = Date.parse(startedAt);
  if (!type || !sourceSessionId || (type === "duplicate" && !targetSessionId) || !browserRecoveryScope) return null;
  if (!Number.isFinite(startedMs) || Date.now() - startedMs > DASHBOARD_OPERATION_MAX_AGE_MS) return null;
  return { type, sourceSessionId, targetSessionId, browserRecoveryScope, startedAt };
}

function saveDashboardOperation(operation = {}) {
  const normalized = normalizeDashboardOperation({ ...operation, browserRecoveryScope: currentBrowserRecoveryScope(), startedAt: operation.startedAt || new Date().toISOString() });
  state.dashboardOperation = normalized;
  try {
    if (normalized) window.localStorage.setItem(DASHBOARD_OPERATION_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // Refresh recovery is best-effort; the active operation still continues in this page.
  }
  return normalized;
}

function clearDashboardOperation() {
  state.dashboardOperation = null;
  try {
    window.localStorage.removeItem(DASHBOARD_OPERATION_STORAGE_KEY);
  } catch {
    // Ignore storage failures after a terminal dashboard operation.
  }
}

function restoredDashboardOperation() {
  let parsed = null;
  try {
    parsed = JSON.parse(window.localStorage.getItem(DASHBOARD_OPERATION_STORAGE_KEY) || "null");
  } catch {
    parsed = null;
  }
  const normalized = normalizeDashboardOperation(parsed || {});
  if (!normalized || normalized.browserRecoveryScope !== currentBrowserRecoveryScope()) {
    clearDashboardOperation();
    return null;
  }
  state.dashboardOperation = normalized;
  return normalized;
}

function dashboardOperationLoadingOptions(operation = state.dashboardOperation) {
  return operation?.type === "duplicate"
    ? { title: "Duplicating quote", message: "Copying the saved quote, files, and pricing state into a new draft." }
    : { title: "Opening quote", message: "Loading the saved quote, files, and pricing state for editing." };
}

function finishDashboardOperation() {
  if (!state.isPageUnloading) clearDashboardOperation();
}

async function resumeDashboardOperation(operation = restoredDashboardOperation()) {
  const normalized = normalizeDashboardOperation(operation || {});
  if (!normalized || normalized.browserRecoveryScope !== currentBrowserRecoveryScope()) return false;
  state.dashboardOperation = normalized;
  setDashboardLoadingState(true, dashboardOperationLoadingOptions(normalized));
  showDashboard({ load: false });
  if (normalized.type === "duplicate") {
    await loadQuoteDashboard({ showLoading: false });
    const existingTarget = state.quoteSessions.find((session) => safeQuoteSessionId(session.session_id || "") === normalized.targetSessionId);
    if (existingTarget) {
      state.dashboardActiveSessionId = normalized.targetSessionId;
      renderQuoteDashboard();
      scrollDashboardSessionIntoView(normalized.targetSessionId);
      clearDashboardOperation();
      setDashboardLoadingState(false);
      return true;
    }
    return duplicateDashboardQuote(normalized.sourceSessionId, { resume: true, targetSessionId: normalized.targetSessionId, operation: normalized });
  }
  return modifyDashboardQuote(normalized.sourceSessionId, { resume: true, operation: normalized });
}

async function modifyDashboardQuote(sessionId, options = {}) {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  const isResume = options.resume === true;
  if (!safeSessionId || (!isResume && appIsBusy()) || state.quoteSessionRestoreBusy) return false;
  const operation = isResume ? normalizeDashboardOperation(options.operation || state.dashboardOperation || {}) : saveDashboardOperation({ type: "modify", sourceSessionId: safeSessionId });
  if (!operation) return false;
  state.dashboardOperation = operation;
  clearQuoteSessionDraftSaveTimer();
  state.quoteSessionRestoreBusy = true;
  setDashboardLoadingState(true, dashboardOperationLoadingOptions(operation));
  try {
    const detailedSession = await loadQuoteSessionDetail(safeSessionId);
    let draftState = detailedSession?.draft_state && typeof detailedSession.draft_state === "object" ? detailedSession.draft_state : {};
    if (!Object.keys(draftState).length && safeSessionId === safeQuoteSessionId(state.quoteSessionId) && quoteDraftShouldPersistToDashboard()) draftState = currentQuoteSessionDraftState();
    if (!Object.keys(draftState).length) {
      mergeDashboardQuoteSession({ ...(detailedSession || {}), session_id: safeSessionId, has_draft_state: false });
      finishDashboardOperation();
      dashboardRestoreError("This quote session does not have saved draft data to modify.");
      return false;
    }
    const draftFiles = Array.isArray(detailedSession?.draft_files) ? detailedSession.draft_files : [];
    if (draftFiles.length) {
      await persistSessionFiles(draftFiles).catch(() => {});
      draftState = mergeDashboardDraftImagesWithAvailablePayloads(draftState, draftFiles);
    }
    draftState = mergeDashboardDraftSummaryDetails(draftState, detailedSession || {});
    draftState = hydrateDashboardDraftImagePayloads(draftState, safeSessionId);
    const restored = await applyQuoteSessionSnapshot({ ...draftState, quoteSessionId: safeSessionId }, { sessionId: safeSessionId, forceQuoteView: true, restoreFurthestPanel: true });
    if (!restored) {
      finishDashboardOperation();
      dashboardRestoreError("This quote session was saved with an incompatible draft format.");
      return false;
    }
    state.dashboardSelectionMode = false;
    state.dashboardSelectedSessionIds = [];
    state.dashboardActiveSessionId = safeSessionId;
    mergeDashboardQuoteSession({ ...detailedSession, has_draft_state: true });
    rememberRestoredQuoteSessionBaseline(safeSessionId);
    clearDashboardOperation();
    showQuoteFlow();
    return true;
  } finally {
    state.quoteSessionRestoreBusy = false;
    setDashboardLoadingState(false);
  }
}

async function duplicateDashboardQuote(sessionId, options = {}) {
  const sourceSessionId = safeQuoteSessionId(sessionId || "");
  const isResume = options.resume === true;
  if (!sourceSessionId || (!isResume && appIsBusy()) || state.quoteSessionRestoreBusy) return false;
  const targetSessionId = safeQuoteSessionId(options.targetSessionId || "") || newClientQuoteSessionId();
  const operation = isResume ? normalizeDashboardOperation(options.operation || state.dashboardOperation || {}) : saveDashboardOperation({ type: "duplicate", sourceSessionId, targetSessionId });
  if (!operation) return false;
  state.dashboardOperation = operation;
  clearQuoteSessionDraftSaveTimer();
  state.quoteSessionRestoreBusy = true;
  setDashboardLoadingState(true, dashboardOperationLoadingOptions(operation));
  try {
    const detailedSession = await loadQuoteSessionDetail(sourceSessionId);
    let draftState = detailedSession?.draft_state && typeof detailedSession.draft_state === "object" ? detailedSession.draft_state : {};
    if (!Object.keys(draftState).length && sourceSessionId === safeQuoteSessionId(state.quoteSessionId) && quoteDraftShouldPersistToDashboard()) draftState = currentQuoteSessionDraftState();
    if (!Object.keys(draftState).length) {
      mergeDashboardQuoteSession({ ...(detailedSession || {}), session_id: sourceSessionId, has_draft_state: false });
      finishDashboardOperation();
      dashboardRestoreError("This quote session does not have saved draft data to duplicate.");
      return false;
    }
    const draftFiles = Array.isArray(detailedSession?.draft_files) ? detailedSession.draft_files : [];
    if (draftFiles.length) {
      await persistSessionFiles(draftFiles).catch(() => {});
      draftState = mergeDashboardDraftImagesWithAvailablePayloads(draftState, draftFiles);
    }
    draftState = mergeDashboardDraftSummaryDetails(draftState, detailedSession || {});
    draftState = hydrateDashboardDraftImagePayloads(draftState, sourceSessionId);
    const restored = await applyQuoteSessionSnapshot({ ...draftState, quoteSessionId: operation.targetSessionId, quoteSessionDraftSaveStarted: true }, { sessionId: operation.targetSessionId, forceQuoteView: false });
    if (!restored) {
      finishDashboardOperation();
      dashboardRestoreError("This quote session was saved with an incompatible draft format.");
      return false;
    }
    state.quoteSessionDraftSaveStarted = true;
    state.quoteSessionRestoredSessionId = "";
    state.quoteSessionRestoredDraftKey = "";
    const duplicatedSession = await saveCurrentQuoteSession({ sessionId: operation.targetSessionId, includeDraftState: true });
    if (!duplicatedSession) {
      if (!state.isPageUnloading) {
        clearDashboardOperation();
        dashboardRestoreError(state.quoteSessionLoadError || "The duplicated quote could not be saved.");
      }
      return false;
    }
    state.dashboardSelectionMode = false;
    state.dashboardSelectedSessionIds = [];
    state.dashboardActiveSessionId = safeQuoteSessionId(duplicatedSession.session_id || operation.targetSessionId);
    state.activeAppView = "dashboard";
    clearDashboardOperation();
    renderQuoteDashboard();
    scrollDashboardSessionIntoView(state.dashboardActiveSessionId);
    return true;
  } finally {
    state.quoteSessionRestoreBusy = false;
    setDashboardLoadingState(false);
  }
}
function dashboardExportAvailabilityItem(session = {}, kind = "xlsx", label = "XLSX") {
  const exportInfo = quoteSessionExport(session, kind);
  const generatedStatus = quoteSessionStatus(session).key === "generated";
  if (exportInfo.exists && exportInfo.url) {
    return { kind, label, exportInfo, available: true, statusText: `${label} ready`, className: "is-available" };
  }
  if (exportInfo.stale) {
    return { kind, label, exportInfo, available: false, statusText: `${label} needs regeneration`, className: "is-unavailable" };
  }
  if (exportInfo.missing) {
    const statusText = generatedStatus ? `${label} needs regeneration` : `Missing ${label}`;
    return { kind, label, exportInfo, available: false, statusText, className: "is-unavailable" };
  }
  const statusText = generatedStatus ? `${label} needs regeneration` : `${label} unavailable`;
  return { kind, label, exportInfo, available: false, statusText, className: "is-unavailable" };
}

function dashboardSessionCard(session = {}) {
  const status = quoteSessionStatus(session);
  const customer = dashboardSessionCustomerText(session);
  const project = dashboardSessionProjectText(session);
  const showName = dashboardSessionShowNameText(session);
  const projectNumber = dashboardSessionProjectNumberText(session);
  const profile = session.quote_company_profile?.display_name || "Quote Company";
  const safeSessionId = safeQuoteSessionId(session.session_id || "");
  const shortReference = dashboardShortSessionReference(session);
  const createdText = formatDashboardDateTime(session.created_at);
  const modifiedText = dashboardModifiedText(session);
  const grandTotal = formatDashboardMoney(session);
  const subtotal = formatDashboardSubtotal(session);
  const grandTotalHtml = grandTotal === "-" ? "&mdash;" : escapeHtml(grandTotal);
  const subtotalHtml = subtotal === "-" ? "&mdash;" : escapeHtml(subtotal);
  const selected = dashboardSelectedSessionIds().includes(safeSessionId);
  const active = safeQuoteSessionId(state.dashboardActiveSessionId || "") === safeSessionId;
  const selectionMode = Boolean(state.dashboardSelectionMode);
  const checkboxLabel = `Select ${customer} ${project}`.trim();
  return `
    <article class="dashboard-session-card${active ? " is-active" : ""}${selected ? " is-selected" : ""}${selectionMode ? " is-selection-mode" : ""}" data-quote-session-id="${escapeHtml(safeSessionId)}" role="button" tabindex="0" aria-selected="${active ? "true" : "false"}">
      <div class="dashboard-session-main">
        <div class="dashboard-session-record-zone dashboard-session-primary-zone">
          <label class="dashboard-session-select-control" data-dashboard-select-control ${selectionMode ? "" : "hidden"}>
            <input type="checkbox" data-dashboard-select aria-label="${escapeHtml(checkboxLabel)}" ${selected ? "checked" : ""}>
            <span aria-hidden="true"></span>
          </label>
          <span class="dashboard-session-file-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" focusable="false">
              <path d="M7 3h7l5 5v13H7V3Z"></path>
              <path d="M14 3v5h5"></path>
            </svg>
          </span>
          <div class="dashboard-session-title-group">
            <strong>${escapeHtml(customer)}</strong>
            <span>${escapeHtml(project)}</span>
            <span class="dashboard-session-show-name">Show name: ${showName ? escapeHtml(showName) : "-"}</span>
            <span class="dashboard-session-project-number">Project number: ${projectNumber ? escapeHtml(projectNumber) : "-"}</span>
          </div>
        </div>
        <dl class="dashboard-session-record-zone dashboard-session-meta-zone">
          <div>
            <dt>Short Ref</dt>
            <dd>${shortReference ? `Ref ${escapeHtml(shortReference)}` : "-"}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>${escapeHtml(createdText)}</dd>
          </div>
          <div class="dashboard-session-total-cell">
            <dt>Total</dt>
            <dd><span class="dashboard-session-total" aria-label="Grand Total ${grandTotal === "-" ? "not available" : escapeHtml(grandTotal)}">${grandTotalHtml}</span></dd>
          </div>
          <div>
            <dt>Quote Company</dt>
            <dd>${escapeHtml(profile)}</dd>
          </div>
          <div>
            <dt>Modified</dt>
            <dd>${escapeHtml(modifiedText)}</dd>
          </div>
          <div class="dashboard-session-subtotal-cell">
            <dt>Subtotal</dt>
            <dd class="dashboard-session-subtotal">${subtotalHtml}</dd>
          </div>
        </dl>
        <div class="dashboard-session-record-zone dashboard-session-result-zone">
          <div class="dashboard-session-status-row">
            ${dashboardSessionProgressPill(session)}
            <span class="dashboard-status-pill ${escapeHtml(status.className)}">${escapeHtml(status.label)}</span>
          </div>
        </div>
      </div>
    </article>
  `;
}

function dashboardSelectedSessionIds() {
  const seen = new Set();
  const selected = [];
  (Array.isArray(state.dashboardSelectedSessionIds) ? state.dashboardSelectedSessionIds : []).forEach((value) => {
    const sessionId = safeQuoteSessionId(value || "");
    if (!sessionId || seen.has(sessionId)) return;
    seen.add(sessionId);
    selected.push(sessionId);
  });
  return selected;
}

function dashboardSessionById(sessionId) {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  if (!safeSessionId) return null;
  return state.quoteSessions.find((session) => safeQuoteSessionId(session.session_id || "") === safeSessionId) || null;
}

function dashboardVisibleSessionIds(sessions = pagedDashboardSessions()) {
  return sessions.map((session) => safeQuoteSessionId(session.session_id || "")).filter(Boolean);
}

function scrollDashboardSessionIntoView(sessionId = state.dashboardActiveSessionId) {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  if (!safeSessionId || !elements.dashboardSessionsList) return;
  const card = elements.dashboardSessionsList.querySelector(`[data-quote-session-id="${safeSessionId}"]`);
  card?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
}

function pruneDashboardSelection(visibleSessions = filteredDashboardSessions()) {
  const visibleIds = new Set(dashboardVisibleSessionIds(visibleSessions));
  const selected = dashboardSelectedSessionIds().filter((sessionId) => visibleIds.has(sessionId));
  state.dashboardSelectedSessionIds = selected;
  if (state.dashboardActiveSessionId && !visibleIds.has(safeQuoteSessionId(state.dashboardActiveSessionId || ""))) {
    state.dashboardActiveSessionId = "";
  }
}

function setDashboardSelection(sessionId, options = {}) {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  const mode = options.mode || "single";
  state.quoteSessionRestoreError = "";
  if (mode === "clear") {
    state.dashboardSelectedSessionIds = [];
    state.dashboardSelectionMode = false;
    state.dashboardActiveSessionId = "";
    renderQuoteDashboard();
    saveWorkspaceViewState();
    return;
  }
  if (mode === "visible") {
    const visibleIds = dashboardVisibleSessionIds();
    state.dashboardSelectionMode = true;
    state.dashboardSelectedSessionIds = visibleIds;
    state.dashboardActiveSessionId = visibleIds.length === 1 ? visibleIds[0] : "";
    renderQuoteDashboard();
    saveWorkspaceViewState();
    return;
  }
  if (!safeSessionId) return;
  if (mode === "toggle") {
    state.dashboardSelectionMode = true;
    const selected = dashboardSelectedSessionIds();
    let nextSelected;
    if (selected.includes(safeSessionId)) {
      nextSelected = selected.filter((item) => item !== safeSessionId);
    } else {
      nextSelected = [...selected, safeSessionId];
    }
    state.dashboardSelectedSessionIds = nextSelected;
    state.dashboardActiveSessionId = nextSelected.length === 1 ? nextSelected[0] : "";
    state.dashboardSelectionMode = nextSelected.length > 0;
    renderQuoteDashboard();
    saveWorkspaceViewState();
    return;
  }
  state.dashboardSelectionMode = false;
  state.dashboardSelectedSessionIds = [];
  state.dashboardActiveSessionId = safeSessionId;
  renderQuoteDashboard();
  saveWorkspaceViewState();
}

function continueDashboardDraft(sessionId) {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  const session = state.quoteSessions.find((item) => safeQuoteSessionId(item.session_id || "") === safeSessionId);
  if (!safeSessionId || !dashboardSessionCanResume(session) || appIsBusy()) return;
  showQuoteFlow();
}

function dashboardSelectedExportAction(session = {}, kind = "xlsx", label = "XLSX") {
  const item = dashboardExportAvailabilityItem(session, kind, label);
  if (item.available) {
    return `<a class="dashboard-selected-action dashboard-export-link ${escapeHtml(item.className)}" href="${escapeHtml(item.exportInfo.url)}" download title="${escapeHtml(item.statusText)}" aria-label="Download ${escapeHtml(label)}"><span class="dashboard-selected-action-kicker">Download</span><span class="dashboard-selected-action-label">${escapeHtml(label)}</span></a>`;
  }
  return `<span class="dashboard-selected-action dashboard-export-missing ${escapeHtml(item.className)}" aria-disabled="true" title="${escapeHtml(item.statusText)}" aria-label="${escapeHtml(item.statusText)}"><span class="dashboard-selected-action-label">${escapeHtml(label)}</span></span>`;
}

function dashboardSelectedSessions() {
  return dashboardSelectedSessionIds().map(dashboardSessionById).filter(Boolean);
}

function dashboardSelectedItemList(sessions = []) {
  if (!sessions.length) return "";
  return `
    <div class="dashboard-selected-items" aria-label="Selected quote sessions">
      <span>Selected items</span>
      <ul>
        ${sessions.slice(0, 6).map((session) => {
          const customer = dashboardSessionCustomerText(session);
          const project = dashboardSessionProjectText(session);
          const shortReference = dashboardShortSessionReference(session);
          return `
            <li>
              <div>
                <strong>${escapeHtml(customer)}</strong>
                <span>${escapeHtml(project)}</span>
                ${shortReference ? `<small>Ref ${escapeHtml(shortReference)}</small>` : ""}
              </div>
              <button class="dashboard-selected-item-remove" type="button" data-dashboard-remove-selected="${escapeHtml(safeQuoteSessionId(session.session_id || ""))}" aria-label="Remove ${escapeHtml(customer)} from selection">x</button>
            </li>
          `;
        }).join("")}
      </ul>
      ${sessions.length > 6 ? `<p>${sessions.length - 6} more selected.</p>` : ""}
    </div>
  `;
}

function dashboardSelectedCloseButton(label = "Clear selection") {
  return `
    <button class="icon-button dashboard-selected-close" type="button" data-dashboard-panel-action="clear-selection" aria-label="${escapeHtml(label)}">
      <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">
        <path d="M6 6l12 12"></path>
        <path d="M18 6L6 18"></path>
      </svg>
    </button>
  `;
}

function renderDashboardBulkPanel(selectedIds = []) {
  const sessions = dashboardSelectedSessions();
  const total = sessions.length;
  elements.dashboardSelectedSessionPanel.innerHTML = `
    <header class="dashboard-selected-header">
      <div>
        <p class="workspace-pane-eyebrow">LOCAL QUOTE HISTORY</p>
        <h3 id="dashboardSelectedSessionTitle">Bulk selection</h3>
        <div class="dashboard-bulk-selection-summary" role="status">
          <strong>${total}</strong>
          <span>quote session${total === 1 ? "" : "s"} selected</span>
        </div>
      </div>
      ${dashboardSelectedCloseButton("Clear selected sessions")}
    </header>
    <div class="dashboard-selected-body dashboard-selected-body--bulk">
      ${dashboardSelectedItemList(sessions)}
    </div>
    <div class="dashboard-selected-actions">
      <button class="secondary-button danger-button dashboard-delete-action" type="button" data-dashboard-panel-action="delete-selected">Delete selected</button>
      <button class="secondary-button" type="button" data-dashboard-panel-action="clear-selection">Clear selection</button>
    </div>
  `;
}

function renderDashboardSinglePanel(activeSession = {}) {
  const status = quoteSessionStatus(activeSession);
  const customer = dashboardSessionCustomerText(activeSession);
  const project = dashboardSessionProjectText(activeSession);
  const showName = dashboardSessionShowNameText(activeSession);
  const projectNumber = dashboardSessionProjectNumberText(activeSession);
  const profile = activeSession.quote_company_profile?.display_name || "Quote Company";
  const pricing = activeSession.pricing_reference?.display_name || "Pricing Reference";
  const safeSessionId = safeQuoteSessionId(activeSession.session_id || "");
  const shortReference = dashboardShortSessionReference(activeSession);
  const createdText = formatDashboardDateTime(activeSession.created_at);
  const modifiedText = dashboardModifiedText(activeSession);
  const canModify = dashboardSessionCanModify(activeSession);
  const modifyTitle = canModify ? "Load the saved quote draft for editing." : "No saved draft data is available for this session.";
  const restoreError = String(state.quoteSessionRestoreError || "").trim();
  elements.dashboardSelectedSessionPanel.innerHTML = `
    <header class="dashboard-selected-header">
      <div>
        <p class="workspace-pane-eyebrow">SELECTED SESSION</p>
        <h3 id="dashboardSelectedSessionTitle">${escapeHtml(customer)}</h3>
        <p class="settings-note">${escapeHtml(project)}</p>
        <p class="dashboard-selected-created"><span>Created ${escapeHtml(createdText)}</span><span>Modified ${escapeHtml(modifiedText)}</span></p>
      </div>
      ${dashboardSelectedCloseButton("Clear selected session")}
    </header>
    <div class="dashboard-selected-body dashboard-selected-body--single">
      <div class="dashboard-selected-status-row">
        ${dashboardSessionProgressPill(activeSession)}
        <span class="dashboard-status-pill ${escapeHtml(status.className)}">${escapeHtml(status.label)}</span>
        ${shortReference ? `<span class="dashboard-selected-reference" title="Ref ${escapeHtml(shortReference)}" aria-label="Ref ${escapeHtml(shortReference)}">${escapeHtml(shortReference)}</span>` : ""}
      </div>
      <dl class="dashboard-selected-summary-grid">
        <div><dt>Grand Total</dt><dd><span class="dashboard-money">${escapeHtml(formatDashboardMoney(activeSession))}</span></dd></div>
        <div><dt>Subtotal</dt><dd>${escapeHtml(formatDashboardSubtotal(activeSession))}</dd></div>
        <div><dt>Currency / FX</dt><dd>${escapeHtml(`${dashboardSessionCurrency(activeSession)} / FX ${quoteExchangeRateText(activeSession.commercials?.exchange_rate ?? 1)}`)}</dd></div>
        <div><dt>Pricing Reference</dt><dd>${escapeHtml(pricing)}</dd></div>
        <div><dt>Tax / Rate</dt><dd>${escapeHtml(`${dashboardTaxText(activeSession)} / ${dashboardTaxRateText(activeSession)}`)}</dd></div>
        <div><dt>Show Name</dt><dd>${showName ? escapeHtml(showName) : "-"}</dd></div>
        <div><dt>Project Number</dt><dd>${projectNumber ? escapeHtml(projectNumber) : "-"}</dd></div>
        <div class="dashboard-selected-company-card"><dt>Quote Company</dt><dd>${escapeHtml(profile)}</dd></div>
      </dl>
      <div class="dashboard-selected-downloads">
        <div class="dashboard-export-action-row">
          ${dashboardSelectedExportAction(activeSession, "xlsx", "XLSX")}
          ${dashboardSelectedExportAction(activeSession, "pdf", "PDF")}
        </div>
        ${restoreError ? `<p class="dashboard-restore-message" role="status">${escapeHtml(restoreError)}</p>` : ""}
      </div>
    </div>
    <div class="dashboard-selected-actions dashboard-selected-actions--single">
      <div class="dashboard-selected-action-row">
      <button class="secondary-button sample-button dashboard-selected-action dashboard-duplicate-action" type="button" data-dashboard-panel-action="duplicate-session" data-quote-session-id="${escapeHtml(safeSessionId)}" ${canModify ? "" : "disabled aria-disabled=\"true\""} title="${escapeHtml(canModify ? "Create a duplicated quote draft." : modifyTitle)}">Duplicate Quote</button>
      <button class="primary-button dashboard-selected-action dashboard-modify-action" type="button" data-dashboard-panel-action="modify-session" data-quote-session-id="${escapeHtml(safeSessionId)}" ${canModify ? "" : "disabled aria-disabled=\"true\""} title="${escapeHtml(modifyTitle)}">Modify quote</button>
      </div>
      <button class="secondary-button danger-button dashboard-delete-action" type="button" data-dashboard-panel-action="delete-session" data-quote-session-id="${escapeHtml(safeSessionId)}">Delete session</button>
      <button class="secondary-button dashboard-selected-action dashboard-clear-selection-action" type="button" data-dashboard-panel-action="clear-selection">Clear selection</button>
    </div>
  `;
}

function renderDashboardSelectedPanel() {
  const selectedIds = dashboardSelectedSessionIds();
  const activeSession = selectedIds.length === 1
    ? dashboardSessionById(selectedIds[0])
    : dashboardSessionById(state.dashboardActiveSessionId);
  const showSelected = selectedIds.length > 1 || Boolean(activeSession);
  if (elements.dashboardEmptySelectionPanel) elements.dashboardEmptySelectionPanel.hidden = showSelected;
  if (!elements.dashboardSelectedSessionPanel) return;
  elements.dashboardSelectedSessionPanel.hidden = !showSelected;
  if (!showSelected) {
    elements.dashboardSelectedSessionPanel.innerHTML = "";
    return;
  }
  if (selectedIds.length > 1) {
    renderDashboardBulkPanel(selectedIds);
    return;
  }
  renderDashboardSinglePanel(activeSession);
}

function renderDashboardSelectionControls(filtered) {
  const visibleIds = dashboardVisibleSessionIds(filtered);
  const selected = dashboardSelectedSessionIds();
  const hasVisibleRows = visibleIds.length > 0 && !state.quoteSessionLoadError;
  const hasStoredSessions = state.quoteSessions.length > 0 && !state.quoteSessionLoadError;
  const selectedSet = new Set(selected);
  const allVisibleSelected = hasVisibleRows && visibleIds.every((sessionId) => selectedSet.has(sessionId));
  if (elements.dashboardSelectionToolbar) elements.dashboardSelectionToolbar.hidden = !hasStoredSessions;
  if (elements.dashboardSelectModeButton) {
    elements.dashboardSelectModeButton.textContent = state.dashboardSelectionMode ? "Select all visible" : "Select";
    elements.dashboardSelectModeButton.disabled = !hasVisibleRows || state.quoteSessionDeleteBusy;
    elements.dashboardSelectModeButton.setAttribute("aria-disabled", String(elements.dashboardSelectModeButton.disabled));
    elements.dashboardSelectModeButton.setAttribute("aria-pressed", String(state.dashboardSelectionMode));
    elements.dashboardSelectModeButton.setAttribute("aria-label", state.dashboardSelectionMode ? "Select all visible quote sessions" : "Enable quote session selection");
    elements.dashboardSelectModeButton.classList.toggle("is-selecting", state.dashboardSelectionMode);
    elements.dashboardSelectModeButton.classList.toggle("is-all-selected", allVisibleSelected);
  }
  if (elements.dashboardSelectionHint) {
    elements.dashboardSelectionHint.hidden = !state.dashboardSelectionMode;
  }
}

async function deleteQuoteSessionById(sessionId) {
  const safeSessionId = safeQuoteSessionId(sessionId || "");
  if (!safeSessionId) return false;
  const url = `/api/quote-sessions/${encodeURIComponent(safeSessionId)}`;
  const headers = {};
  if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
  let response;
  let data;
  try {
    response = await fetch(url, { method: "DELETE", headers });
    data = await jsonFromResponse(response);
    if (response.status === 403 && await refreshSessionToken()) {
      const retryHeaders = {};
      if (state.csrfToken) retryHeaders[state.csrfHeaderName] = state.csrfToken;
      response = await fetch(url, { method: "DELETE", headers: retryHeaders });
      data = await jsonFromResponse(response);
    }
  } catch (error) {
    return { ok: false, data: error };
  }
  return { ok: response.ok, data };
}

function quoteSessionDeletePendingIds() {
  const seen = new Set();
  const sessionIds = [];
  (Array.isArray(state.quoteSessionDeletePendingIds) ? state.quoteSessionDeletePendingIds : []).forEach((value) => {
    const sessionId = safeQuoteSessionId(value || "");
    if (!sessionId || seen.has(sessionId)) return;
    seen.add(sessionId);
    sessionIds.push(sessionId);
  });
  return sessionIds;
}

function hideQuoteSessionDeleteModal() {
  if (state.quoteSessionDeleteBusy) return;
  state.quoteSessionDeletePendingIds = [];
  state.quoteSessionDeleteBulk = false;
  if (elements.quoteSessionDeleteError) {
    elements.quoteSessionDeleteError.hidden = true;
    elements.quoteSessionDeleteError.textContent = "";
  }
  if (elements.quoteSessionDeleteModal) {
    elements.quoteSessionDeleteModal.classList.remove("is-open");
    elements.quoteSessionDeleteModal.hidden = true;
  }
}

function renderQuoteSessionDeleteModal() {
  const sessionIds = quoteSessionDeletePendingIds();
  const modal = elements.quoteSessionDeleteModal;
  if (!modal || !sessionIds.length) {
    hideQuoteSessionDeleteModal();
    return;
  }
  const bulk = state.quoteSessionDeleteBulk || sessionIds.length > 1;
  if (elements.quoteSessionDeleteTitle) elements.quoteSessionDeleteTitle.textContent = bulk ? "Delete selected quote sessions?" : "Delete quote session?";
  if (elements.quoteSessionDeleteText) {
    elements.quoteSessionDeleteText.textContent = bulk ? QUOTE_SESSION_DELETE_BULK_MESSAGE : QUOTE_SESSION_DELETE_SINGLE_MESSAGE;
  }
  if (elements.confirmQuoteSessionDeleteButton) {
    elements.confirmQuoteSessionDeleteButton.textContent = bulk ? "Delete selected" : "Delete session";
    elements.confirmQuoteSessionDeleteButton.disabled = state.quoteSessionDeleteBusy;
    elements.confirmQuoteSessionDeleteButton.setAttribute("aria-disabled", String(state.quoteSessionDeleteBusy));
  }
  if (elements.cancelQuoteSessionDeleteButton) {
    elements.cancelQuoteSessionDeleteButton.disabled = state.quoteSessionDeleteBusy;
    elements.cancelQuoteSessionDeleteButton.setAttribute("aria-disabled", String(state.quoteSessionDeleteBusy));
  }
  modal.hidden = false;
  modal.classList.add("is-open");
  queueActionButtonFocus(elements.confirmQuoteSessionDeleteButton);
}

function requestQuoteSessionDelete(sessionIds, options = {}) {
  if (appIsBusy() || state.quoteSessionDeleteBusy) return;
  const ids = Array.isArray(sessionIds) ? sessionIds : [sessionIds];
  state.quoteSessionDeletePendingIds = ids.map((value) => safeQuoteSessionId(value || "")).filter(Boolean);
  state.quoteSessionDeleteBulk = Boolean(options.bulk);
  if (elements.quoteSessionDeleteError) {
    elements.quoteSessionDeleteError.hidden = true;
    elements.quoteSessionDeleteError.textContent = "";
  }
  renderQuoteSessionDeleteModal();
}

async function confirmQuoteSessionDelete() {
  const sessionIds = quoteSessionDeletePendingIds();
  if (!sessionIds.length || state.quoteSessionDeleteBusy) {
    hideQuoteSessionDeleteModal();
    return;
  }
  state.quoteSessionDeleteBusy = true;
  renderQuoteSessionDeleteModal();
  let failed = null;
  for (const sessionId of sessionIds) {
    const result = await deleteQuoteSessionById(sessionId);
    if (!result?.ok) {
      failed = result?.data || {};
      break;
    }
  }
  state.quoteSessionDeleteBusy = false;
  if (failed) {
    if (elements.quoteSessionDeleteError) {
      elements.quoteSessionDeleteError.textContent = genericFailureMessage(failed);
      elements.quoteSessionDeleteError.hidden = false;
    }
    renderQuoteSessionDeleteModal();
    return;
  }
  if (sessionIds.includes(safeQuoteSessionId(state.quoteSessionId))) {
    state.quoteSessionId = "";
    saveSessionState();
  }
  state.dashboardSelectedSessionIds = dashboardSelectedSessionIds().filter((sessionId) => !sessionIds.includes(sessionId));
  if (sessionIds.includes(safeQuoteSessionId(state.dashboardActiveSessionId))) {
    state.dashboardActiveSessionId = state.dashboardSelectedSessionIds.length === 1 ? state.dashboardSelectedSessionIds[0] : "";
  }
  if (!state.dashboardSelectedSessionIds.length) {
    state.dashboardSelectionMode = false;
  }
  state.quoteSessionDeletePendingIds = [];
  state.quoteSessionDeleteBulk = false;
  if (elements.quoteSessionDeleteModal) {
    elements.quoteSessionDeleteModal.classList.remove("is-open");
    elements.quoteSessionDeleteModal.hidden = true;
  }
  await loadQuoteDashboard();
  syncControlStates();
}

function handleDashboardSelectModeButton() {
  if (appIsBusy() || state.quoteSessionDeleteBusy) return;
  if (!state.dashboardSelectionMode) {
    const activeId = safeQuoteSessionId(state.dashboardActiveSessionId || "");
    const visibleIds = dashboardVisibleSessionIds();
    state.dashboardSelectionMode = true;
    state.dashboardSelectedSessionIds = activeId && visibleIds.includes(activeId) ? [activeId] : [];
    renderQuoteDashboard();
    saveWorkspaceViewState();
    return;
  }
  const visibleIds = dashboardVisibleSessionIds();
  const selectedSet = new Set(dashboardSelectedSessionIds());
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((sessionId) => selectedSet.has(sessionId));
  if (allVisibleSelected) {
    state.dashboardSelectedSessionIds = [];
    state.dashboardActiveSessionId = "";
    state.dashboardSelectionMode = false;
    renderQuoteDashboard();
    saveWorkspaceViewState();
    return;
  }
  setDashboardSelection("", { mode: "visible" });
}

function handleDashboardSessionAction(event) {
  const selectInput = event.target?.closest?.("[data-dashboard-select]");
  if (selectInput && elements.dashboardSessionsList?.contains(selectInput)) {
    const card = selectInput.closest("[data-quote-session-id]");
    setDashboardSelection(card?.dataset?.quoteSessionId || "", { mode: "toggle" });
    return;
  }
  const selectControl = event.target?.closest?.("[data-dashboard-select-control]");
  if (selectControl && elements.dashboardSessionsList?.contains(selectControl)) {
    event.preventDefault();
    const card = selectControl.closest("[data-quote-session-id]");
    setDashboardSelection(card?.dataset?.quoteSessionId || "", { mode: "toggle" });
    return;
  }
  const card = event.target?.closest?.("[data-quote-session-id]");
  if (!card || !elements.dashboardSessionsList?.contains(card)) return;
  const safeSessionId = safeQuoteSessionId(card.dataset.quoteSessionId || "");
  const activeId = safeQuoteSessionId(state.dashboardActiveSessionId || "");
  const mode = (state.dashboardSelectionMode || (safeSessionId && safeSessionId === activeId)) ? "toggle" : "single";
  setDashboardSelection(safeSessionId, { mode });
}

function handleDashboardOutsideSelectionClick(event) {
  if (!state.dashboardSelectionMode || state.activeAppView !== "dashboard") return;
  if (state.quoteSessionDeleteBusy || appIsBusy()) return;
  const target = event.target;
  const path = typeof event.composedPath === "function" ? event.composedPath() : [];
  if (path.some((node) => (
    node?.id === "dashboardSessionsList"
    || node?.id === "dashboardSidePanel"
    || node?.classList?.contains?.("dashboard-list-toolbar")
    || node?.classList?.contains?.("modal-overlay")
  ))) return;
  if (target?.closest?.(".dashboard-list-toolbar, #dashboardSessionsList, #dashboardSidePanel, .modal-overlay")) return;
  setDashboardSelection("", { mode: "clear" });
}

function handleDashboardSessionKeydown(event) {
  if (!["Enter", " "].includes(event.key)) return;
  if (event.target?.closest?.("input, button, a, select, textarea")) return;
  const card = event.target?.closest?.("[data-quote-session-id]");
  if (!card || !elements.dashboardSessionsList?.contains(card)) return;
  event.preventDefault();
  const safeSessionId = safeQuoteSessionId(card.dataset.quoteSessionId || "");
  const selectedIds = dashboardSelectedSessionIds();
  const activeId = safeQuoteSessionId(state.dashboardActiveSessionId || "");
  const singleSelectedId = selectedIds.length === 1 ? selectedIds[0] : activeId;
  if (event.key === "Enter" && !state.dashboardSelectionMode && safeSessionId && safeSessionId === singleSelectedId && dashboardSessionCanModify(dashboardSessionById(safeSessionId))) {
    modifyDashboardQuote(safeSessionId);
    return;
  }
  setDashboardSelection(safeSessionId, { mode: state.dashboardSelectionMode ? "toggle" : "single" });
}

function handleDashboardListArrowKey(event) {
  if (!["ArrowUp", "ArrowDown"].includes(event.key) || event.defaultPrevented) return false;
  if (state.activeAppView !== "dashboard") return false;
  if (event.target?.closest?.("input, button, a, select, textarea, [contenteditable='true']")) return false;
  if (profileActionsMenuIsOpen()) return false;
  if (elements.pricingReferenceTableOverlay && !elements.pricingReferenceTableOverlay.hidden) return false;
  if (elements.basisChatOverlay && !elements.basisChatOverlay.hidden) return false;
  if (elements.profileLoadModal && !elements.profileLoadModal.hidden) return false;
  if (elements.profileOverwriteModal && !elements.profileOverwriteModal.hidden) return false;
  if (elements.profileNameModal && !elements.profileNameModal.hidden) return false;
  if (elements.outputDeleteModal && !elements.outputDeleteModal.hidden) return false;
  if (elements.quoteSessionDeleteModal && !elements.quoteSessionDeleteModal.hidden) return false;
  if (elements.profileDeleteModal && !elements.profileDeleteModal.hidden) return false;
  if (elements.pricingReferenceModal && !elements.pricingReferenceModal.hidden) return false;
  if (elements.analysisConfirmModal && !elements.analysisConfirmModal.hidden) return false;

  const visibleIds = dashboardVisibleSessionIds();
  if (!visibleIds.length) return false;
  const activeId = safeQuoteSessionId(state.dashboardActiveSessionId || "");
  const selectedIds = dashboardSelectedSessionIds();
  const currentId = activeId || (selectedIds.length === 1 ? selectedIds[0] : "");
  const currentIndex = visibleIds.indexOf(currentId);
  let nextIndex = 0;
  if (currentIndex >= 0) {
    nextIndex = event.key === "ArrowUp"
      ? Math.max(0, currentIndex - 1)
      : Math.min(visibleIds.length - 1, currentIndex + 1);
  }
  event.preventDefault();
  const nextId = visibleIds[nextIndex];
  setDashboardSelection(nextId, { mode: "single" });
  scrollDashboardSessionIntoView(nextId);
  return true;
}

function handleDashboardEnterKey(event) {
  if (event.key !== "Enter" || event.defaultPrevented) return false;
  if (state.activeAppView !== "dashboard") return false;
  if (event.target?.closest?.("input, button, a, select, textarea, [contenteditable='true']")) return false;
  if (profileActionsMenuIsOpen()) return false;
  if (elements.pricingReferenceTableOverlay && !elements.pricingReferenceTableOverlay.hidden) return false;
  if (elements.basisChatOverlay && !elements.basisChatOverlay.hidden) return false;
  if (elements.profileLoadModal && !elements.profileLoadModal.hidden) return false;
  if (elements.profileOverwriteModal && !elements.profileOverwriteModal.hidden) return false;
  if (elements.profileNameModal && !elements.profileNameModal.hidden) return false;
  if (elements.outputDeleteModal && !elements.outputDeleteModal.hidden) return false;
  if (elements.quoteSessionDeleteModal && !elements.quoteSessionDeleteModal.hidden) return false;
  if (elements.profileDeleteModal && !elements.profileDeleteModal.hidden) return false;
  if (elements.pricingReferenceModal && !elements.pricingReferenceModal.hidden) return false;
  if (elements.analysisConfirmModal && !elements.analysisConfirmModal.hidden) return false;

  const selectedIds = dashboardSelectedSessionIds();
  if (selectedIds.length > 1) return false;
  const selectedId = safeQuoteSessionId(selectedIds[0] || state.dashboardActiveSessionId || "");
  if (!selectedId) return false;
  if (!dashboardSessionCanModify(dashboardSessionById(selectedId))) return false;
  event.preventDefault();
  modifyDashboardQuote(selectedId);
  return true;
}

function handleDashboardDeleteKey(event) {
  if (event.key !== "Delete" || event.defaultPrevented) return false;
  if (state.activeAppView !== "dashboard") return false;
  const selectionControl = event.target?.closest?.("[data-dashboard-select], [data-dashboard-select-control]");
  if (event.target?.closest?.("input, select, textarea, [contenteditable='true']") && !selectionControl) return false;
  if (elements.quoteSessionDeleteModal && !elements.quoteSessionDeleteModal.hidden) return false;
  const selectedIds = dashboardSelectedSessionIds();
  const activeId = safeQuoteSessionId(state.dashboardActiveSessionId || "");
  const deleteIds = selectedIds.length ? selectedIds : (activeId ? [activeId] : []);
  if (!deleteIds.length) return false;
  event.preventDefault();
  requestQuoteSessionDelete(deleteIds, { bulk: deleteIds.length > 1 });
  return true;
}

function handleDashboardSidePanelAction(event) {
  const removeSelected = event.target?.closest?.("[data-dashboard-remove-selected]");
  if (removeSelected && elements.dashboardSidePanel?.contains(removeSelected)) {
    event.preventDefault();
    setDashboardSelection(removeSelected.dataset.dashboardRemoveSelected || "", { mode: "toggle" });
    return;
  }
  const action = event.target?.closest?.("[data-dashboard-panel-action]");
  if (!action || !elements.dashboardSidePanel?.contains(action)) return;
  const selectedId = safeQuoteSessionId(state.dashboardActiveSessionId || dashboardSelectedSessionIds()[0] || "");
  if (action.dataset.dashboardPanelAction === "modify-session") {
    modifyDashboardQuote(action.dataset.quoteSessionId || selectedId);
  } else if (action.dataset.dashboardPanelAction === "duplicate-session") {
    duplicateDashboardQuote(action.dataset.quoteSessionId || selectedId);
  } else if (action.dataset.dashboardPanelAction === "continue-session") {
    continueDashboardDraft(selectedId);
  } else if (action.dataset.dashboardPanelAction === "delete-session") {
    requestQuoteSessionDelete(action.dataset.quoteSessionId || selectedId);
  } else if (action.dataset.dashboardPanelAction === "delete-selected") {
    requestQuoteSessionDelete(dashboardSelectedSessionIds(), { bulk: true });
  } else if (action.dataset.dashboardPanelAction === "clear-selection") {
    setDashboardSelection("", { mode: "clear" });
  }
}

function syncDashboardCustomDateRangeControls() {
  const isCustom = (state.dashboardDateFilter || "all") === "custom";
  if (elements.dashboardCustomDateRange) elements.dashboardCustomDateRange.hidden = !isCustom;
  if (elements.dashboardDateStartInput) elements.dashboardDateStartInput.value = state.dashboardCustomDateStart || "";
  if (elements.dashboardDateEndInput) elements.dashboardDateEndInput.value = state.dashboardCustomDateEnd || "";
}

function dashboardDateFilterSummaryText(filteredCount = 0, totalCount = 0) {
  const filtered = Math.max(0, Number(filteredCount) || 0);
  const total = Math.max(0, Number(totalCount) || 0);
  const noun = total === 1 ? "session" : "sessions";
  if (!state.dashboardCustomDateStart && !state.dashboardCustomDateEnd) return `Date filter: all ${total} ${noun}`;
  return `Date filter: ${filtered} of ${total} ${noun}`;
}

function renderQuoteDashboard() {
  updateDashboardSummary();
  if (elements.dashboardDateFilter) elements.dashboardDateFilter.value = state.dashboardDateFilter || "all";
  ensureDashboardCustomDateDefaults();
  syncDashboardCustomDateRangeControls();
  if (elements.dashboardSortSelect) elements.dashboardSortSelect.value = state.dashboardSortMode || "created";
  if (elements.dashboardStatusFilter) elements.dashboardStatusFilter.value = state.dashboardStatusFilter || "all";
  const filtered = filteredDashboardSessions();
  const paged = pagedDashboardSessions(filtered);
  const range = dashboardPageRange(filtered.length);
  if (elements.dashboardDateFilterSummary) {
    elements.dashboardDateFilterSummary.textContent = dashboardDateFilterSummaryText(filtered.length, state.quoteSessions.length);
  }
  pruneDashboardSelection(filtered);
  const hasError = Boolean(state.quoteSessionLoadError);
  const hasSessions = state.quoteSessions.length > 0;
  const hasRows = filtered.length > 0;
  if (elements.dashboardSessionCount) {
    elements.dashboardSessionCount.textContent = hasError
      ? "Sessions could not be loaded."
      : hasSessions
        ? filtered.length > 0
          ? filtered.length === state.quoteSessions.length
            ? `Showing ${range.start}-${range.end} of ${state.quoteSessions.length} session${state.quoteSessions.length === 1 ? "" : "s"}.`
            : `Showing ${range.start}-${range.end} of ${filtered.length} matching session${filtered.length === 1 ? "" : "s"}.`
          : "No matching sessions."
        : "No saved sessions in this local runtime.";
  }
  renderDashboardPageControls(filtered);
  if (elements.dashboardErrorState) elements.dashboardErrorState.hidden = !hasError;
  if (elements.dashboardErrorText) elements.dashboardErrorText.textContent = state.quoteSessionLoadError || GENERIC_FAILURE_MESSAGE;
  if (elements.dashboardEmptyState) {
    elements.dashboardEmptyState.hidden = hasError || hasRows;
    if (elements.dashboardEmptyEyebrow) elements.dashboardEmptyEyebrow.textContent = hasSessions ? "NO MATCHES" : "QUOTE LIST";
    const strong = elements.dashboardEmptyState.querySelector("strong");
    const detail = elements.dashboardEmptyState.querySelector("p");
    if (strong) strong.textContent = hasSessions ? "No matching quote sessions" : "No quote sessions yet";
    if (detail) detail.textContent = hasSessions ? "Adjust the filter or search terms." : "Start a new quote to create the first local session.";
    const emptyAction = elements.dashboardEmptyState.querySelector("button");
    if (emptyAction) emptyAction.hidden = hasSessions;
  }
  if (elements.dashboardSessionsList) {
    elements.dashboardSessionsList.hidden = hasError || !hasRows;
    elements.dashboardSessionsList.innerHTML = paged.map(dashboardSessionCard).join("");
  }
  renderDashboardSelectionControls(paged);
  renderDashboardSelectedPanel();
}

function delay(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function startJob(type, payload, options = {}) {
  return postJson("/api/jobs", {
    type,
    payload,
    ...(options.jobId ? { job_id: options.jobId } : {}),
  });
}

async function pollJob(jobId, onStatus) {
  const maxFetchFailures = 4;
  let fetchFailures = 0;
  while (jobId) {
    const url = `/api/jobs/${encodeURIComponent(jobId)}`;
    const { ok, data } = await getJson(url, { logFetchFailure: false });
    if (!ok) {
      if (data?.page_unloading) return { ok, data, aborted: true };
      if (data?.fetch_failed && fetchFailures < maxFetchFailures) {
        fetchFailures += 1;
        if (typeof onStatus === "function") onStatus({ status: "retrying", fetch_failures: fetchFailures });
        await delay(Math.min(1000 * fetchFailures, 4000));
        continue;
      }
      if (data?.fetch_failed) {
        logClientEvent("client_error", fetchFailureLogDetails(url, {
          error_reference: errorReferenceFrom(data),
          attempts: fetchFailures + 1,
        }));
      }
      return { ok, data };
    }
    fetchFailures = 0;
    if (typeof onStatus === "function") onStatus(data);
    if (FINAL_JOB_STATUSES.has(data.status)) return { ok: true, data };
    await delay(900);
  }
  return { ok: false, data: { status: "failed", errors: genericFailureMessages() } };
}

function isInterruptedJobPoll(polled) {
  return Boolean(polled?.aborted || polled?.data?.fetch_failed);
}

function handleInterruptedJobPoll(jobType = "draft", polled = {}) {
  const data = polled?.data || polled || {};
  if (jobType === "draft") {
    state.isAnalysisRunning = false;
    showAiFailureBanner(genericFailureMessage(data));
    setWorkflowStage("analyzing");
  } else {
    state.isGenerating = false;
    setResultStatus("Connection interrupted", "is-warn");
    renderMessages(genericFailureMessages(data), "error");
  }
  syncControlStates();
}

function logClientEvent(event, details = {}) {
  if (state.isPageUnloading) return;
  const allowedEvents = new Set(["client_error", "server_error", "security_event", "abuse_signal"]);
  if (!allowedEvents.has(event)) return;
  const headers = { "Content-Type": "application/json" };
  if (state.csrfToken) headers[state.csrfHeaderName] = state.csrfToken;
  fetch("/api/log", {
    method: "POST",
    headers,
    body: JSON.stringify({ event, details }),
  }).catch(() => {});
}

async function initializeSession() {
  const { ok, data } = await getJson("/api/session");
  if (ok && await applySessionData(data)) {
    return;
  }
  if (elements.healthText) elements.healthText.textContent = "Local session unavailable";
}

function syncControlStates() {
  const busy = appIsBusy();
  elements.newQuoteButton.disabled = busy;
  elements.newQuoteButton.hidden = state.activeAppView !== "dashboard";
  elements.newQuoteButton.title = busy ? appBusyTitle() : "";
  if (elements.topbarBrandButton) {
    elements.topbarBrandButton.disabled = busy;
    elements.topbarBrandButton.setAttribute("aria-disabled", String(busy));
    elements.topbarBrandButton.title = busy ? appBusyTitle() : "Open dashboard";
  }
  [elements.dashboardEmptyNewQuoteButton, elements.backToDashboardButton]
    .filter(Boolean)
    .forEach((button) => {
      button.disabled = busy;
      button.setAttribute("aria-disabled", String(busy));
      button.title = busy ? appBusyTitle() : "";
    });
  renderDashboardSelectionControls(pagedDashboardSessions(filteredDashboardSessions()));
  if (elements.backToDashboardButton) {
    elements.backToDashboardButton.hidden = state.activeAppView !== "quote";
  }
  if (elements.settingsButton) {
    const canManage = canManagePricingReferences();
    elements.settingsButton.hidden = false;
    elements.settingsButton.disabled = busy || !canManage;
    elements.settingsButton.title = canManage ? "Pricing reference settings" : pricingReferenceNoAccessReason();
    elements.settingsButton.setAttribute("aria-disabled", String(elements.settingsButton.disabled));
  }
  updatePresetButtons();
  updateSidePanelNav();
  saveSessionState();
}

async function handleDraftBasis(options = {}) {
  if (state.isAnalysisRunning) return;
  const analysisMode = normalizeAnalysisMode(options.analysisMode || state.pendingAnalysisMode);
  state.pendingAnalysisMode = analysisMode;
  const runningMessage = analysisRunningMessage(analysisMode);
  const hasFeedback = Boolean(state.pendingFeedback.trim()) || options.finalAfterClarifications;
  const analysisRequestedAt = new Date().toISOString();

  if (!state.images.length) {
    setWorkflowStage("needs_images");
    showBlockedAction(`Please drop at least one ${currentGenerator().imageNoun} before analysis.`, { details: false });
    syncControlStates();
    return;
  }
  const missing = missingDetailFields();
  if (missing.length) {
    setWorkflowStage("details_review");
    showBlockedAction(`Fill Customer and Quote Company before AI analysis: ${missing.join(", ")}.`);
    renderBasisEmptyState("Complete the missing Customer and Quote Company details, then start analysis to draft the quote basis.");
    setDetailsDrawer(true);
    syncControlStates();
    return;
  }

  const operation = normalizeActiveJob({
    id: newClientJobId(),
    type: "draft",
    phase: "starting",
    startedAt: analysisRequestedAt,
    analysisMode,
    includeBoothDimensions: state.boothDimensions.dimension_source !== "default",
    includeDraftContext: hasFeedback,
    finalAfterClarifications: options.finalAfterClarifications === true,
  });
  const quoteCommercialSnapshot = quoteCommercialOverrideSnapshot();
  state.activeJob = operation;
  state.isAnalysisRunning = true;
  state.aiFailed = false;
  state.draftSource = "";
  state.lastAnalysisMode = analysisMode;
  clearAiFailureBanner();
  setWorkflowStage("analyzing");
  showAiRunningBanner(runningMessage, analysisRequestedAt);
  clearBasisReviewSurface();
  setSidePanel("basis", { force: true });
  syncControlStates();
  await ensureQuoteSession({ quoteGenerated: false });

  const started = await startJob("draft", buildPayload({
    analysisMode,
    includeBoothDimensions: state.boothDimensions.dimension_source !== "default",
    includeDraftContext: hasFeedback,
  }), { jobId: operation.id });
  if (!started.ok) {
    if (started.data?.page_unloading) return;
    state.isAnalysisRunning = false;
    clearActiveJob();
    const errors = started.data.errors || ["Draft failed."];
    const wasBlocked = started.data.status === "blocked";
    setWorkflowStage(wasBlocked ? "details_review" : "ready_to_analyze");
    if (wasBlocked) {
      showAiBlockedBanner(errors.join(" "));
    } else {
      showAiFailureBanner(genericFailureMessage(started.data));
    }
    restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "draft_start_failed" });
    syncControlStates();
    return;
  }
  const startedAt = started.data.created_at || analysisRequestedAt;
  state.activeJob = { ...operation, id: started.data.job_id || operation.id, phase: "running", startedAt };
  showAiRunningBanner(runningMessage, startedAt);
  saveSessionState();

  const polled = await pollJob(state.activeJob.id);
  if (polled.aborted) {
    restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "draft_poll_aborted" });
    return;
  }
  if (isInterruptedJobPoll(polled)) {
    handleInterruptedJobPoll("draft", polled);
    restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "draft_poll_interrupted" });
    return;
  }
  state.isAnalysisRunning = false;
  clearActiveJob();

  if (!polled.ok || ["blocked", "failed"].includes(polled.data.status)) {
    const errors = polled.data.errors || polled.data.result?.errors || ["Draft failed."];
    const wasBlocked = polled.data.status === "blocked";
    setWorkflowStage(wasBlocked ? "details_review" : "ready_to_analyze");
    if (wasBlocked) {
      showAiBlockedBanner(errors.join(" "));
    } else {
      showAiFailureBanner(genericFailureMessage(polled.data.result || polled.data));
    }
    restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "draft_poll_failed" });
    syncControlStates();
    return;
  }

  const data = polled.data.result || {};
  if (Array.isArray(data.blocking_clarification_questions) && data.blocking_clarification_questions.length) {
    clearAiFailureBanner();
    openBlockingClarifications(data.blocking_clarification_questions, data.analysis_findings || state.analysisFindings || [], data.project || state.boothDimensions || {});
    restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "draft_blocking_clarification" });
    await saveQuoteSessionDraftState({ quoteGenerated: false });
    return;
  }
  const hasFinalBasis = Array.isArray(data.quote_basis_sections) && data.quote_basis_sections.length && Array.isArray(data.line_items) && data.line_items.length;
  if (options.finalAfterClarifications && !hasFinalBasis) {
    renderClarificationQuestions();
    restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "draft_missing_final_basis" });
    return;
  }
  state.blockingClarificationQuestions = [];
  state.analysisFindings = data.analysis_findings || [];
  const aiFailed = Boolean(data.ai_failed || polled.data.status === "degraded" || (data.source === "local" && Array.isArray(data.warnings) && data.warnings.length));
  if (aiFailed) {
    showAiFailedDraftState(data);
    restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "draft_ai_failed" });
    await saveQuoteSessionDraftState({ quoteGenerated: false });
    return;
  }
  applyDraftBasis(data);
  applyDraftLineItems(data.line_items || []);
  state.boothDimensions = normalizeBoothDimensions(data.project || state.boothDimensions);
  state.draftSource = data.source || "";
  state.lastAnalysisMode = normalizeAnalysisMode(data.analysis_mode || analysisMode);
  captureOriginalAnalysisSnapshot(data);
  state.aiFailed = false;
  restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "draft_success" });
  setWorkflowStage("basis_review");
  clearAiFailureBanner();
  updateQuoteBasisCard(data.source);
  setSidePanel("basis", { force: true });
  syncControlStates();
  await saveQuoteSessionDraftState({ quoteGenerated: false });
}

async function confirmBasis(options = {}) {
  if (appIsBusy()) {
    if (options.resume === true) clearActiveJob();
    return;
  }
  const restoredOperation = options.resume === true ? normalizeActiveJob(options.operation || state.activeJob || {}) : null;
  const operation = restoredOperation?.type === "confirm_basis" ? restoredOperation : normalizeActiveJob({
    id: newClientOperationId(),
    type: "confirm_basis",
    phase: "running",
    startedAt: new Date().toISOString(),
  });
  const confirmBlockReason = basisConfirmBlockReason();
  if (confirmBlockReason) {
    setWorkflowStage("basis_review");
    showBlockedBasisAction(confirmBlockReason);
    syncControlStates();
    if (restoredOperation) clearActiveJob();
    return;
  }
  if (state.aiFailed) {
    setWorkflowStage("basis_review");
    showAiFailureBanner();
    syncControlStates();
    if (restoredOperation) clearActiveJob();
    return;
  }
  if (!state.lineItems.length) {
    setWorkflowStage("basis_review");
    showBlockedBasisAction("Quote basis has no line items ready for output. Re-run analysis or resolve the quote basis before confirming.");
    syncControlStates();
    if (restoredOperation) clearActiveJob();
    return;
  }
  const missing = missingDetailFields();
  if (missing.length) {
    setWorkflowStage("details_review");
    await saveQuoteSessionDraftState({ quoteGenerated: false });
    showBlockedBasisAction(`Complete Customer and Quote Company details before confirming quotation basis: ${missing.join(", ")}.`);
    syncControlStates();
    if (restoredOperation) clearActiveJob();
    return;
  }
  state.activeJob = operation;
  state.isPreparingOutput = true;
  showExcelGeneratingModal({
    eyebrow: "Quotation output",
    title: "Preparing Output",
    message: "Building the pricing review rows. Excel download will be available after this finishes.",
  });
  syncControlStates();
  await waitForUiPaint();
  try {
    const refreshed = await refreshLineItemsFromServer();
    if (!refreshed.ok) {
      if (refreshed.data?.page_unloading) {
        return;
      }
      clearActiveJob();
      state.basisConfirmed = false;
      setWorkflowStage("basis_review");
      setResultStatus("Failed", "is-bad");
      showBlockedBasisAction(genericFailureMessage(refreshed.data));
      return;
    }
    state.basisConfirmed = true;
    refreshOutputRowsFromLineItems();
    state.originalOutputRows = snapshotOutputRows(state.outputRows);
    state.lineItems = outputRowsToLineItems();
    setWorkflowStage("completed");
    await saveQuoteSessionDraftState({ quoteGenerated: true });
    clearActiveJob();
    renderPricingMatches(state.outputRows);
    renderMatchSummary({ pricing_matches: state.outputRows });
    setResultStatus("Ready for pricing review", "is-warn");
    renderOutputValidationMessages(outputRowsValid().errors);
    setSidePanel("output", { force: true });
  } finally {
    state.isPreparingOutput = false;
    if (!state.isPageUnloading) hideExcelGeneratingModal();
    syncControlStates();
  }
}

async function handleGenerate(options = {}) {
  if (state.isGenerating) return;
  const viewPdf = options.viewPdf === true;
  if (state.activeSidePanel === "output") {
    const validation = outputRowsValid();
    if (!validation.valid) {
      renderOutputValidationMessages(validation.errors);
      setResultStatus("Output needs review", "is-warn");
      syncControlStates();
      return;
    }
    state.lineItems = outputRowsToLineItems();
  }
  if (!state.basisConfirmed) {
    if (hasSubmittedQuoteBasis()) {
      setWorkflowStage("basis_review");
      setSidePanel("basis", { force: true });
    }
    syncControlStates();
    return;
  }
  if (state.aiFailed) {
    setWorkflowStage("basis_review");
    showAiFailureBanner();
    syncControlStates();
    return;
  }
  const missing = missingDetailFields();
  if (missing.length) {
    setWorkflowStage("details_review");
    await saveQuoteSessionDraftState({ quoteGenerated: Boolean(state.basisConfirmed || state.outputRows.length) });
    showBlockedAction(`Complete Customer and Quote Company details before generating quotation: ${missing.join(", ")}.`);
    syncControlStates();
    return;
  }
  if (!state.lineItems.length) {
    setWorkflowStage("basis_review");
    return;
  }

  const jobType = viewPdf ? "generate_pdf" : "generate";
  const operation = normalizeActiveJob({
    id: newClientJobId(),
    type: jobType,
    phase: "starting",
    startedAt: new Date().toISOString(),
    viewPdf,
  });
  state.activeJob = operation;
  state.isGenerating = true;
  setWorkflowStage("generating");
  setResultStatus(viewPdf ? "Generating PDF" : "Generating Excel", "is-warn");
  renderMessages([]);
  setDownloadFiles([]);
  renderMatchSummary({});
  clearPricingReviewMessages();
  syncControlStates();
  await ensureQuoteSession({ quoteGenerated: true });
  const started = await startJob(jobType, buildPayload({ viewPdf }), { jobId: operation.id });
  if (!started.ok) {
    if (started.data?.page_unloading) return;
    state.isGenerating = false;
    clearActiveJob();
    setWorkflowStage(state.activeSidePanel === "output" ? "completed" : "details_review");
    setResultStatus(started.data.status || "Failed", "is-bad");
    renderMessages(started.data.status === "blocked" ? (started.data.errors || ["Generation blocked."]) : genericFailureMessages(started.data), "error");
    syncControlStates();
    return;
  }
  state.activeJob = { ...operation, id: started.data.job_id || operation.id, phase: "running" };
  saveSessionState();

  const polled = await pollJob(state.activeJob.id);
  if (polled.aborted) return;
  if (isInterruptedJobPoll(polled)) {
    handleInterruptedJobPoll(jobType, polled);
    return;
  }
  state.isGenerating = false;
  clearActiveJob();

  const data = polled.data.result || polled.data || {};
  if (data.quote_session?.session_id) {
    state.quoteSessionId = safeQuoteSessionId(data.quote_session.session_id) || state.quoteSessionId;
  }
  if (!polled.ok || ["blocked", "failed"].includes(polled.data.status) || data.status === "blocked" || data.status === "failed") {
    setWorkflowStage(state.activeSidePanel === "output" ? "completed" : "details_review");
    setResultStatus(data.status || "Failed", "is-bad");
    const blocked = polled.data.status === "blocked" || data.status === "blocked";
    renderMessages(blocked ? (data.errors || ["Generation blocked."]) : genericFailureMessages(data || polled.data), "error");
    if (data.pricing_matches?.length) renderPricingMatches(data.pricing_matches || [], { fromPricingMatches: true });
    renderMatchSummary(data);
    syncControlStates();
    return;
  }

  const needsPricingReview = polled.data.status === "needs_review"
    || data.status === "needs_confirmation";
  if (needsPricingReview) {
    setWorkflowStage("completed");
    setResultStatus("Needs pricing review", "is-warn");
    renderMessages([]);
    clearPricingReviewMessages();
    setSidePanel("output", { force: true });
    setDownloadFiles([]);
    if (data.pricing_matches?.length) renderPricingMatches(data.pricing_matches || [], { fromPricingMatches: true });
    renderMatchSummary(data);
  } else {
    setWorkflowStage("completed");
    clearPricingReviewMessages();
    setSidePanel("output", { force: true });
    setDownloadFiles(data.files || []);
    renderPricingMatches(state.outputRows);
    renderMatchSummary({ pricing_matches: state.outputRows });
    if (viewPdf && !state.pdfFile) {
      setResultStatus("PDF unavailable", "is-bad");
      const pdfStatus = data.export_status?.pdf_status || data.export_status?.pdf_readiness || "";
      const reason = pdfStatus === "workbook_export_unavailable"
        ? "Workbook PDF export was unavailable, so no PDF was created."
        : "The export completed but did not return a PDF file.";
      renderMessages([`${reason} Download Excel is still available.`], "error");
    } else {
      setResultStatus(viewPdf ? "PDF ready" : "Completed", "is-ok");
      renderMessages([]);
    }
  }
  await saveQuoteSessionDraftState({ quoteGenerated: true });
  syncControlStates();
  return viewPdf ? Boolean(state.pdfFile) : Boolean(state.downloadFile);
}

async function ensureSavedServerJobStarted(activeJob) {
  if (activeJob.phase !== "starting") return { ok: true, data: { job_id: activeJob.id } };
  const isDraft = activeJob.type === "draft";
  const viewPdf = activeJob.type === "generate_pdf" || activeJob.viewPdf === true;
  let payload;
  if (isDraft) {
    payload = buildPayload({
      analysisMode: normalizeAnalysisMode(activeJob.analysisMode),
      includeBoothDimensions: activeJob.includeBoothDimensions === true,
      includeDraftContext: activeJob.includeDraftContext === true,
    });
  } else {
    payload = buildPayload({ viewPdf });
  }
  await ensureQuoteSession({ quoteGenerated: !isDraft });
  const started = await startJob(activeJob.type, payload, { jobId: activeJob.id });
  if (!started.ok) return started;
  state.activeJob = {
    ...activeJob,
    id: started.data.job_id || activeJob.id,
    phase: "running",
    startedAt: started.data.created_at || activeJob.startedAt,
  };
  saveSessionState();
  return started;
}

async function resumeSavedJob() {
  const activeJob = state.activeJob;
  if (!activeJob || !activeJob.id) return;
  if (activeJob.type === "confirm_basis") {
    state.isPreparingOutput = false;
    await confirmBasis({ resume: true, operation: activeJob });
    return;
  }

  if (activeJob.type === "basis_chat") {
    const restored = restoreBasisChatOverlay();
    if (!restored) {
      clearActiveJob();
      return;
    }
    const text = String(activeJob.text || "").trim();
    if (text) appendBasisChatMessage("user", text);
    const aiResult = await buildAiBasisChatResponse(text, { resume: true, operation: activeJob });
    if (aiResult?.proposal) setBasisChatProposal(aiResult.proposal);
    else if (aiResult?.answer) appendBasisChatMessage("assistant", aiResult.answer);
    else if (!aiResult?.errorDisplayed && !aiResult?.preserved) {
      appendBasisChatMessage("assistant", "I could not produce a useful basis change. Please rephrase the request.");
    }
    return;
  }
  if (activeJob.type === "draft") {
    const quoteCommercialSnapshot = quoteCommercialOverrideSnapshot();
    state.isAnalysisRunning = true;
    state.isGenerating = false;
    setWorkflowStage("analyzing");
    showAiRunningBanner(ANALYSIS_WAIT_ESTIMATE, activeJobStartedAt(activeJob));
    clearBasisReviewSurface();
    setSidePanel("basis", { force: true });
    syncControlStates();
    const restarted = await ensureSavedServerJobStarted(activeJob);
    if (!restarted.ok) {
      if (restarted.data?.page_unloading) return;
      state.isAnalysisRunning = false;
      clearActiveJob();
      setWorkflowStage("ready_to_analyze");
      showAiFailureBanner(genericFailureMessage(restarted.data));
      restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "resume_draft_start_failed" });
      syncControlStates();
      return;
    }
    const resumedJob = state.activeJob || activeJob;

    const polled = await pollJob(resumedJob.id, (job) => {
      if (job.created_at && !state.activeJob?.startedAt) {
        state.activeJob = { ...state.activeJob, startedAt: job.created_at };
        showAiRunningBanner(ANALYSIS_WAIT_ESTIMATE, job.created_at);
        saveSessionState();
      }
    });
    if (polled.aborted) {
      restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "resume_draft_poll_aborted" });
      return;
    }
    if (isInterruptedJobPoll(polled)) {
      handleInterruptedJobPoll("draft", polled);
      restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "resume_draft_poll_interrupted" });
      return;
    }
    state.isAnalysisRunning = false;
    clearActiveJob();

    if (!polled.ok || ["blocked", "failed"].includes(polled.data.status)) {
      setWorkflowStage("ready_to_analyze");
      showAiFailureBanner(genericFailureMessage(polled.data.result || polled.data));
      restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "resume_draft_failed" });
      syncControlStates();
      return;
    }

    const data = polled.data.result || {};
    const aiFailed = Boolean(data.ai_failed || polled.data.status === "degraded" || (data.source === "local" && Array.isArray(data.warnings) && data.warnings.length));
    if (aiFailed) {
      showAiFailedDraftState(data);
      restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "resume_draft_ai_failed" });
      await saveQuoteSessionDraftState({ quoteGenerated: false });
      return;
    }
    applyDraftBasis(data);
    applyDraftLineItems(data.line_items || []);
    state.boothDimensions = normalizeBoothDimensions(data.project || state.boothDimensions);
    state.draftSource = data.source || "";
    captureOriginalAnalysisSnapshot(data);
    state.aiFailed = false;
    restoreQuoteCommercialOverrideSnapshot(quoteCommercialSnapshot, { reason: "resume_draft_success" });
    setWorkflowStage("basis_review");
    clearAiFailureBanner();
    updateQuoteBasisCard(data.source);
    setSidePanel("basis", { force: true });
    syncControlStates();
    await saveQuoteSessionDraftState({ quoteGenerated: false });
    return;
  }

  if (activeJob.type === "generate" || activeJob.type === "generate_pdf") {
    const viewPdf = activeJob.viewPdf === true || activeJob.type === "generate_pdf";
    state.isGenerating = true;
    state.isAnalysisRunning = false;
    setWorkflowStage("generating");
    setResultStatus(viewPdf ? "Checking PDF" : "Checking Excel", "is-warn");
    setSidePanel("output", { force: true });
    showExcelGeneratingModal(generationLoadingModalOptions(viewPdf));
    syncControlStates();
    const restarted = await ensureSavedServerJobStarted(activeJob);
    if (!restarted.ok) {
      if (restarted.data?.page_unloading) return;
      state.isGenerating = false;
      clearActiveJob();
      hideExcelGeneratingModal();
      setWorkflowStage("completed");
      setResultStatus(restarted.data.status || "Failed", "is-bad");
      renderMessages(genericFailureMessages(restarted.data), "error");
      syncControlStates();
      return;
    }
    const resumedJob = state.activeJob || activeJob;

    const polled = await pollJob(resumedJob.id);
    if (polled.aborted) return;
    if (isInterruptedJobPoll(polled)) {
      hideExcelGeneratingModal();
      handleInterruptedJobPoll(activeJob.type, polled);
      return;
    }

    const data = polled.data.result || polled.data || {};
    if (data.quote_session?.session_id) {
      state.quoteSessionId = safeQuoteSessionId(data.quote_session.session_id) || state.quoteSessionId;
    }
    if (!polled.ok || ["blocked", "failed"].includes(polled.data.status) || data.status === "blocked" || data.status === "failed") {
      state.isGenerating = false;
      clearActiveJob();
      hideExcelGeneratingModal();
      setWorkflowStage("details_review");
      setResultStatus(data.status || "Failed", "is-bad");
      const blocked = polled.data.status === "blocked" || data.status === "blocked";
      renderMessages(blocked ? (data.errors || ["Generation blocked."]) : genericFailureMessages(data || polled.data), "error");
      if (data.pricing_matches?.length) renderPricingMatches(data.pricing_matches || [], { fromPricingMatches: true });
      renderMatchSummary(data);
      syncControlStates();
      return;
    }

    const needsPricingReview = polled.data.status === "needs_review"
      || data.status === "needs_confirmation";
    showExcelGeneratingModal(generationFinalizingModalOptions(viewPdf));
    if (needsPricingReview) {
      setWorkflowStage("completed");
      setResultStatus("Needs pricing review", "is-warn");
      renderMessages([]);
      clearPricingReviewMessages();
      setSidePanel("output", { force: true });
      setDownloadFiles([]);
    } else {
      setWorkflowStage("completed");
      setResultStatus(viewPdf ? "PDF ready" : "Completed", "is-ok");
      renderMessages([]);
      clearPricingReviewMessages();
      setSidePanel("output");
      setDownloadFiles(data.files || []);
    }
    if (data.pricing_matches?.length) renderPricingMatches(data.pricing_matches || [], { fromPricingMatches: true });
    renderMatchSummary(data);
    await saveQuoteSessionDraftState({ quoteGenerated: true });
    state.isGenerating = false;
    clearActiveJob();
    syncControlStates();
    if (!needsPricingReview && showGeneratedExportReadyModal(viewPdf)) return;
    hideExcelGeneratingModal();
    return;
  }

  clearActiveJob();
  syncControlStates();
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (elements.statusDot) elements.statusDot.classList.toggle("is-ok", response.ok);
    if (elements.topbarStatus) elements.topbarStatus.classList.toggle("is-ok", response.ok);
    if (elements.healthText) elements.healthText.textContent = response.ok ? "Ready" : data.error || "Unavailable";
  } catch {
    if (elements.healthText) elements.healthText.textContent = "Unavailable";
    if (elements.topbarStatus) elements.topbarStatus.classList.remove("is-ok");
  }
}

function requestStartAnalysis(mode = "standard") {
  const reason = startAnalysisBlockReason();
  if (reason) {
    showBlockedAction(reason);
    syncControlStates();
    return;
  }
  state.pendingAnalysisMode = normalizeAnalysisMode(mode);
  state.restorableOverlay = "analysis_confirm";
  elements.analysisConfirmModal.hidden = false;
  elements.analysisConfirmModal.classList.add("is-open");
  saveSessionState();
  window.setTimeout(() => elements.analysisConfirmStartButton?.focus(), 0);
}

function closeAnalysisConfirmModal() {
  elements.analysisConfirmModal.classList.remove("is-open");
  elements.analysisConfirmModal.hidden = true;
  if (state.restorableOverlay === "analysis_confirm") {
    state.restorableOverlay = "";
    saveSessionState();
  }
}

function confirmStartAnalysis(mode = state.pendingAnalysisMode) {
  closeAnalysisConfirmModal();
  handleDraftBasis({ analysisMode: normalizeAnalysisMode(mode) });
}

function isSensitiveChatRequest(normalizedText) {
  return [
    "api key",
    ".env",
    "authorization",
    "bearer",
    "secret",
    "token",
    "system prompt",
    "hidden prompt",
    "developer message",
    "internal prompt",
  ].some((term) => normalizedText.includes(term));
}

function sidePanelBlockReason(panelName) {
  if (panelName === "images") return "";
  if (!hasReferenceFilesForNavigation()) return "Add reference files before opening this step.";
  if (quoteOutputProgressForNavigation()) return "";
  if (panelName === "quote_company") {
    return customerDetailsBlockReason("Complete Customer details before opening Quote Company");
  }
  if (panelName === "basis") {
    const missing = missingDetailFields();
    if (missing.length) return `Complete Customer and Quote Company details before opening Quote Basis: ${missing.join(", ")}.`;
    if (!hasSubmittedQuoteBasis()) return "Click Start Analysis from Quote Company before opening Quote Basis.";
  }
  if (panelName === "output") {
    const missing = missingDetailFields();
    if (missing.length) return `Complete Customer and Quote Company details before opening Output: ${missing.join(", ")}.`;
    if (!hasSubmittedQuoteBasis()) return "Click Start Analysis from Quote Company before opening Output.";
    if ((state.blockingClarificationQuestions || []).some((question) => question.status !== "answered" && !String(question.answer || "").trim())) return "Answer all clarification questions before opening Output.";
    const confirmBlockReason = basisConfirmBlockReason();
    if (confirmBlockReason) return confirmBlockReason;
    if (!hasCompletedQuoteBasis()) return "Confirm Quotation Basis before opening Output.";
  }
  return "";
}

function resetQuoteFlowScroll() {
  const scrollTargets = [elements.sideWorkspace, document.querySelector(".workspace-pane-scroll")].filter(Boolean);
  scrollTargets.forEach((target) => {
    if (typeof target.scrollTo === "function") {
      target.scrollTo({ top: 0, left: 0, behavior: "auto" });
    } else {
      target.scrollTop = 0;
    }
  });
  window.scrollTo?.({ top: 0, left: 0, behavior: "auto" });
}

function setSidePanel(panelName, options = {}) {
  const panelTitles = {
    images: ["Upload", "Reference Inputs", currentGenerator().intakeSubtitle],
    customer: ["Customer", "Customer Details", "Customer, project, booth size, and customer address for this quotation."],
    quote_company: ["Quote Company", "Quotation Defaults", "Reusable quotation-company header, payment terms, notes, and signature defaults."],
    basis: ["Quote Basis", "Confirm Draft", "Review the drafted basis and revise individual lines where needed."],
    output: ["Output", "Editable Pricing", "Review quotation rows, resolve pricing, and download Excel."],
  };
  const nextPanel = panelTitles[panelName] ? panelName : "images";
  const blockReason = sidePanelBlockReason(nextPanel);
  if (blockReason && !options.force) {
    updateSidePanelNav();
    return false;
  }
  const previousPanel = state.activeSidePanel;
  const [title, eyebrow, subtitle] = panelTitles[nextPanel] || panelTitles.images;
  state.activeSidePanel = nextPanel;
  if (
    state.activeSidePanel === "customer"
    && !QUOTE_COMMERCIAL_FIELD_KEYS.some((field) => quoteCommercialFieldIsTouched(field))
  ) {
    resetQuoteCommercialFieldsToSelectedPricingReference();
  }
  document.body.dataset.sidePanel = state.activeSidePanel;
  elements.sideDrawerTitle.textContent = title;
  elements.sideDrawerEyebrow.textContent = eyebrow;
  elements.sideDrawerSubtitle.textContent = subtitle || "";
  document.querySelectorAll("button[data-side-panel]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.sidePanel === state.activeSidePanel);
  });
  document.querySelectorAll(".side-panel-section[data-side-panel-content]").forEach((section) => {
    section.classList.toggle("is-active", section.dataset.sidePanelContent === state.activeSidePanel);
  });
  if (state.activeSidePanel === "basis" && (state.blockingClarificationQuestions || []).length && !state.quoteBasisSections.length && !state.lineItems.length) {
    renderClarificationQuestions();
  }
  elements.sideWorkspace.setAttribute("aria-hidden", "false");
  updateSidePanelNav();
  saveSessionState();
  if (nextPanel !== previousPanel) resetQuoteFlowScroll();
  return true;
}

function activeSidePanelIndex() {
  return Math.max(0, SIDE_PANEL_SEQUENCE.indexOf(state.activeSidePanel));
}

function updateSidePanelNav() {
  if (!elements.sideBackButton || !elements.sideNextButton) return;
  const index = activeSidePanelIndex();
  const lastIndex = SIDE_PANEL_SEQUENCE.length - 1;
  const isQuoteCompanyStep = state.activeSidePanel === "quote_company";
  const isBasisStep = state.activeSidePanel === "basis";
  const isOutputStep = state.activeSidePanel === "output";
  const busy = appIsBusy();
  elements.resetImagesButton.hidden = state.activeSidePanel !== "images";
  elements.clearCustomerButton.hidden = state.activeSidePanel !== "customer";
  elements.clearQuoteCompanyButton.hidden = state.activeSidePanel !== "quote_company";
  elements.analyseAgainButton.hidden = state.activeSidePanel !== "basis";
  elements.resetQuoteBasisButton.hidden = state.activeSidePanel !== "basis";
  elements.resetOutputButton.hidden = state.activeSidePanel !== "output";
  elements.resetImagesButton.disabled = busy || !hasReferenceFilesForNavigation();
  elements.clearCustomerButton.disabled = busy;
  elements.clearQuoteCompanyButton.disabled = busy;
  const canReanalyseBasis = hasReferenceFilesForAnalysis();
  elements.analyseAgainButton.disabled = busy || !canReanalyseBasis;
  elements.analyseAgainButton.title = busy
    ? appBusyTitle()
    : canReanalyseBasis
      ? "Re-analyse the quote basis using the uploaded reference images."
      : "Images from this saved quote are unavailable in this browser. Upload the reference images again before re-analysing.";
  elements.analyseAgainButton.setAttribute?.("aria-disabled", String(elements.analyseAgainButton.disabled));
  elements.resetQuoteBasisButton.disabled = busy || !state.originalAnalysisSnapshot;
  elements.resetOutputButton.disabled = busy || !state.originalOutputRows.length;
  document.querySelectorAll("button[data-side-panel]").forEach((button) => {
    const panelName = button.dataset.sidePanel || "images";
    const blockReason = sidePanelBlockReason(panelName);
    const locked = Boolean(blockReason);
    button.disabled = busy || locked;
    button.classList.toggle("is-locked", locked);
    button.setAttribute("aria-disabled", String(button.disabled));
    button.title = locked ? blockReason : "";
  });
  elements.sideBackButton.disabled = index === 0 || busy;
  elements.sideNextButton.hidden = isOutputStep;
  if (elements.workspacePaneFooter) elements.workspacePaneFooter.classList.toggle("is-output-step", isOutputStep);
  if (elements.sideViewPdfButton) elements.sideViewPdfButton.hidden = !isOutputStep;
  elements.sideDownloadButton.hidden = !isOutputStep;
  const nextLabels = {
    images: "Next: Customer",
    customer: "Next: Quote Company",
    quote_company: currentGenerator().analyzeLabel,
    basis: "Confirm Quotation Basis",
  };
  elements.sideNextButton.textContent = nextLabels[state.activeSidePanel] || "Next";
  elements.sideNextButton.classList.add("primary-button");
  elements.sideNextButton.classList.remove("secondary-button");
  const nextPanel = SIDE_PANEL_SEQUENCE[Math.min(index + 1, lastIndex)];
  const basisBlockReason = isBasisStep ? basisConfirmBlockReason() : "";
  let nextBlockReason = "";
  if (busy) {
    nextBlockReason = appBusyTitle();
  } else if (isQuoteCompanyStep) {
    nextBlockReason = startAnalysisBlockReason();
  } else if (isBasisStep) {
    nextBlockReason = basisBlockReason;
  } else {
    nextBlockReason = sidePanelBlockReason(nextPanel);
  }
  const shouldDisableNextButton = state.activeSidePanel === "customer" && Boolean(nextBlockReason);
  elements.sideNextButton.disabled = shouldDisableNextButton;
  elements.sideNextButton.setAttribute("aria-disabled", String(Boolean(nextBlockReason)));
  elements.sideNextButton.classList.toggle("is-disabled", Boolean(nextBlockReason));
  elements.sideNextButton.title = nextBlockReason || "";
  updateDownloadButton();
}

function goToPreviousSidePanel() {
  const index = activeSidePanelIndex();
  if (index <= 0) return;
  setSidePanel(SIDE_PANEL_SEQUENCE[index - 1]);
}

async function goToNextSidePanel() {
  const index = activeSidePanelIndex();
  if (elements.sideNextButton?.getAttribute("aria-disabled") === "true") {
    const reason = elements.sideNextButton.title || "This step is not ready yet.";
    elements.sideNextButton.title = "";
    elements.sideNextButton.blur();
    if (state.activeSidePanel === "quote_company") showBlockedAction(reason);
    if (state.activeSidePanel === "basis") showBlockedBasisAction(reason);
    return;
  }
  if (state.activeSidePanel === "quote_company") {
    requestStartAnalysis();
    return;
  }
  if (state.activeSidePanel === "basis") {
    confirmBasis();
    return;
  }
  if (state.activeSidePanel === "output") return;
  if (state.activeSidePanel === "customer") applyPendingPricingReferenceSelection();
  const nextPanel = SIDE_PANEL_SEQUENCE[index + 1];
  const moved = setSidePanel(nextPanel, { notify: true });
  if (moved && nextPanel === "customer") {
    await startQuoteSessionDraftSaveAfterCustomerStep();
  } else if (moved) {
    await saveQuoteSessionDraftState({ quoteGenerated: Boolean(state.basisConfirmed || state.outputRows.length) });
  }
}

function handleQuoteBasisClick(event) {
  const sectionAction = event.target.closest("[data-basis-section-action]");
  if (sectionAction) {
    retagBasisSectionConfirmLines(
      sectionAction.dataset.basisSection || "",
      sectionAction.dataset.basisSectionAction || ""
    );
    return;
  }
  const possibleMatchButton = event.target.closest("[data-basis-possible-match-index]");
  if (possibleMatchButton) {
    applyPossiblePricingMatch(
      possibleMatchButton.dataset.basisSection || "",
      Number(possibleMatchButton.dataset.basisLineIndex),
      Number(possibleMatchButton.dataset.basisPossibleMatchIndex)
    );
    return;
  }
  const tagButton = event.target.closest("[data-basis-tag]");
  if (tagButton) {
    retagBasisLine(
      tagButton.dataset.basisSection || "",
      Number(tagButton.dataset.basisLineIndex),
      tagButton.dataset.basisTag || ""
    );
    return;
  }
  const reviseButton = event.target.closest("[data-revise-section]");
  if (reviseButton) {
    const sectionId = reviseButton.dataset.reviseSection || "";
    const lineIndex = Number(reviseButton.dataset.reviseLineIndex);
    const section = state.quoteBasisSections.find((item) => item.id === sectionId);
    const line = section?.lines?.[lineIndex];
    openBasisChatOverlay("line", {
      sectionId,
      lineIndex,
      line: line ? basisChatLineContext(line) : "",
      quantity: line?.quantity ?? "",
      unit: line?.unit || "",
      quantityLabel: line ? basisQuantityLabel(line) : "",
    });
    return;
  }
}

function markPageUnloading() {
  state.isPageUnloading = true;
}

function pricingReferenceShouldWarnBeforeUnload() {
  if (!elements.pricingReferenceModal || elements.pricingReferenceModal.hidden) return false;
  if (pricingReferenceOperationBusy()) return true;
  if (!state.pendingPricingReference) return false;
  return pricingReferenceHasPendingChanges(state.pendingPricingReference);
}

function handleBeforeUnload(event) {
  if (state.isRecoveryScopeTransitioning) {
    markPageUnloading();
    return undefined;
  }
  if (!pricingReferenceShouldWarnBeforeUnload()) {
    markPageUnloading();
    return undefined;
  }
  event.preventDefault();
  event.returnValue = "";
  return "";
}

function wireEvents() {
  if (elements.topbarLogoutLink) {
    elements.topbarLogoutLink.addEventListener("click", async (event) => {
      event.preventDefault();
      const href = elements.topbarLogoutLink.getAttribute("href") || "/logout";
      await purgeBrowserRecoveryState();
      window.location.assign(href);
    });
  }
  wireRichTextEditors();
  if (elements.pricingReferenceFile) {
    elements.pricingReferenceFile.accept = PRICING_REFERENCE_FILE_ACCEPT;
  }
  if (elements.imageInput) {
    elements.imageInput.accept = REFERENCE_FILE_ACCEPT;
  }
  window.addEventListener("pagehide", markPageUnloading);
  window.addEventListener("beforeunload", handleBeforeUnload);
  elements.imageInput.addEventListener("change", async (event) => {
    await addImagesFromFiles(event.target.files || []);
    elements.imageInput.value = "";
  });

  elements.headerLogoInput.addEventListener("change", async (event) => {
    const file = (event.target.files || [])[0];
    state.headerLogo = file
      ? {
          name: file.name,
          type: file.type,
          size: file.size,
          data_url: await fileToDataUrl(file),
        }
      : null;
    renderHeaderLogoPreview();
    renderPresetStatus();
    syncControlStates();
  });
  elements.headerLogoPreview.addEventListener("click", () => {
    elements.headerLogoInput.click();
  });
  elements.headerLogoPreview.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    elements.headerLogoInput.click();
  });

  elements.dropzone.addEventListener("dragenter", (event) => {
    event.preventDefault();
    elements.dropzone.classList.add("is-dragging");
  });

  elements.dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    elements.dropzone.classList.add("is-dragging");
  });

  elements.dropzone.addEventListener("dragleave", (event) => {
    if (!elements.dropzone.contains(event.relatedTarget)) {
      elements.dropzone.classList.remove("is-dragging");
    }
  });

  elements.dropzone.addEventListener("drop", async (event) => {
    event.preventDefault();
    elements.dropzone.classList.remove("is-dragging");
    await addImagesFromFiles(event.dataTransfer.files || []);
  });

  elements.fileList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-image]");
    if (!button) return;
    removeImageAt(Number(button.dataset.removeImage));
  });

  document.querySelectorAll("button[data-side-panel]").forEach((button) => {
    button.addEventListener("click", () => {
      const panelName = button.dataset.sidePanel || "images";
      const moved = setSidePanel(panelName, { notify: true });
      if (moved) saveQuoteSessionDraftStateAfterPanelMove(panelName).catch(() => {});
    });
  });
  elements.sideBackButton.addEventListener("click", goToPreviousSidePanel);
  elements.sideNextButton.addEventListener("click", goToNextSidePanel);
  elements.sideDownloadButton.addEventListener("click", async (event) => {
    event.preventDefault();
    if (elements.sideDownloadButton.getAttribute("aria-disabled") === "true") {
      const validation = outputRowsValid();
      if (!validation.valid) renderOutputValidationMessages(validation.errors);
      return;
    }
    commitActiveOutputEditor();
    showExcelGeneratingModal(generationLoadingModalOptions(false));
    await waitForUiPaint();
    try {
      await handleGenerate();
      downloadCurrentExcelFile();
    } finally {
      hideExcelGeneratingModal();
    }
  });
  elements.sideViewPdfButton.addEventListener("click", async (event) => {
    event.preventDefault();
    if (elements.sideViewPdfButton.getAttribute("aria-disabled") === "true") {
      const validation = outputRowsValid();
      if (!validation.valid) renderOutputValidationMessages(validation.errors);
      return;
    }
    commitActiveOutputEditor();
    showExcelGeneratingModal(generationLoadingModalOptions(true));
    await waitForUiPaint();
    try {
      const generated = await handleGenerate({ viewPdf: true });
      if (generated) viewCurrentPdfFile();
    } finally {
      hideExcelGeneratingModal();
    }
  });
  elements.excelGeneratingActionButton?.addEventListener("click", () => {
    handleGeneratedExportReadyAction();
  });
  elements.excelGeneratingCloseButton?.addEventListener("click", () => {
    hideExcelGeneratingModal();
  });
  elements.excelGeneratingModal?.addEventListener("click", (event) => {
    if (
      event.target === elements.excelGeneratingModal
      || event.target.classList?.contains("modal-backdrop")
    ) {
      dismissGeneratedExportReadyModal();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (handleDashboardEnterKey(event)) return;
    if (handleDashboardListArrowKey(event)) return;
    if (handleDashboardDeleteKey(event)) return;
    if (event.key === "Escape") {
      if (dismissGeneratedExportReadyModal()) {
        event.preventDefault();
      } else if (profileActionsMenuIsOpen()) {
        closeProfileActionsMenu({ focusButton: true });
      } else if (elements.pricingReferenceTableOverlay && !elements.pricingReferenceTableOverlay.hidden) {
        closePricingReferenceTableOverlay();
      } else if (!elements.basisChatOverlay.hidden) {
        closeBasisChatOverlay();
      } else if (elements.profileLoadModal && !elements.profileLoadModal.hidden) {
        hideProfileLoadModal({ focusButton: true });
      } else if (elements.profileOverwriteModal && !elements.profileOverwriteModal.hidden) {
        hideProfileOverwriteModal({ focusInput: true });
      } else if (elements.profileNameModal && !elements.profileNameModal.hidden) {
        hideProfileNameModal();
      } else if (elements.outputDeleteModal && !elements.outputDeleteModal.hidden) {
        hideOutputDeleteModal();
      } else if (elements.quoteSessionDeleteModal && !elements.quoteSessionDeleteModal.hidden) {
        hideQuoteSessionDeleteModal();
      } else if (elements.profileDeleteModal && !elements.profileDeleteModal.hidden) {
        hideProfileDeleteModal();
      } else if (elements.pricingReferenceModal && !elements.pricingReferenceModal.hidden) {
        if (elements.pricingReferenceDeleteConfirm && !elements.pricingReferenceDeleteConfirm.hidden) {
          hidePricingReferenceDeleteConfirm();
        } else {
          closePricingReferenceModal();
        }
      } else if (!elements.analysisConfirmModal.hidden) {
        closeAnalysisConfirmModal();
      }
    }
  });

  elements.topbarBrandButton?.addEventListener("click", handleTopbarBrandClick);
  elements.newQuoteButton.addEventListener("click", startNewQuote);
  elements.dashboardEmptyNewQuoteButton?.addEventListener("click", startNewQuote);
  elements.dashboardSessionsList?.addEventListener("click", handleDashboardSessionAction);
  elements.dashboardSessionsList?.addEventListener("keydown", handleDashboardSessionKeydown);
  document.addEventListener("click", handleDashboardOutsideSelectionClick);
  elements.dashboardSidePanel?.addEventListener("click", handleDashboardSidePanelAction);
  elements.dashboardSelectModeButton?.addEventListener("click", handleDashboardSelectModeButton);
  elements.backToDashboardButton?.addEventListener("click", returnToDashboard);
  elements.dashboardStatusFilter?.addEventListener("change", () => {
    state.dashboardStatusFilter = elements.dashboardStatusFilter.value || "all";
    state.dashboardPageIndex = 0;
    renderQuoteDashboard();
    saveWorkspaceViewState();
  });
  elements.dashboardDateFilter?.addEventListener("change", () => {
    state.dashboardDateFilter = elements.dashboardDateFilter.value || "all";
    ensureDashboardCustomDateDefaults();
    state.dashboardPageIndex = 0;
    renderQuoteDashboard();
    saveWorkspaceViewState();
  });
  elements.dashboardDateStartInput?.addEventListener("change", () => {
    state.dashboardCustomDateStart = elements.dashboardDateStartInput.value || "";
    state.dashboardPageIndex = 0;
    renderQuoteDashboard();
    saveWorkspaceViewState();
  });
  elements.dashboardDateEndInput?.addEventListener("change", () => {
    state.dashboardCustomDateEnd = elements.dashboardDateEndInput.value || "";
    state.dashboardPageIndex = 0;
    renderQuoteDashboard();
    saveWorkspaceViewState();
  });
  elements.dashboardSortSelect?.addEventListener("change", () => {
    state.dashboardSortMode = elements.dashboardSortSelect.value || "created";
    state.dashboardPageIndex = 0;
    renderQuoteDashboard();
    saveWorkspaceViewState();
  });
  elements.dashboardSearchInput?.addEventListener("input", () => {
    state.dashboardSearch = elements.dashboardSearchInput.value || "";
    state.dashboardPageIndex = 0;
    renderQuoteDashboard();
    saveWorkspaceViewState();
  });
  elements.dashboardPageSizeSelect?.addEventListener("change", () => {
    state.dashboardPageSize = Number(elements.dashboardPageSizeSelect.value);
    state.dashboardPageIndex = 0;
    renderQuoteDashboard();
    saveWorkspaceViewState();
  });
  elements.dashboardRangeSelect?.addEventListener("change", () => {
    state.dashboardPageIndex = Number(elements.dashboardRangeSelect.value);
    renderQuoteDashboard();
    saveWorkspaceViewState();
  });
  elements.settingsButton?.addEventListener("click", openSettingsModal);
  elements.profileSelect.addEventListener("change", handleProfileSelectionChange);
  elements.newPricingReferenceButton?.addEventListener("click", openPricingReferenceModal);
  elements.pricingReferenceManageTab?.addEventListener("click", () => setPricingReferenceSettingsMode(PRICING_REFERENCE_SETTINGS_MODE_MANAGE, { focus: true }));
  elements.pricingReferenceImportTab?.addEventListener("click", handlePricingReferenceImportTabClick);
  elements.deletePricingReferenceSelect?.addEventListener("change", () => {
    hidePricingReferenceDeleteConfirm();
    updatePricingReferenceDeleteButton();
    editSelectedPricingReference({ openTable: false });
  });
  elements.deletePricingReferenceButton?.addEventListener("click", requestSelectedPricingReferenceDelete);
  elements.cancelPricingReferenceDeleteButton?.addEventListener("click", hidePricingReferenceDeleteConfirm);
  elements.confirmPricingReferenceDeleteButton?.addEventListener("click", deleteSelectedPricingReference);
  elements.outputSortMode?.addEventListener("change", () => { state.outputSortMode = elements.outputSortMode.value; renderPricingMatches(state.outputRows); renderMatchSummary({ pricing_matches: state.outputRows }); syncControlStates(); });
  elements.pricingReferenceForm.addEventListener("submit", savePricingReferenceFromModal);
  elements.exportPricingReferenceButton?.addEventListener("click", exportSelectedPricingReference);
  elements.pricingReferenceTemplateButton.addEventListener("click", downloadPricingReferenceTemplate);
  elements.pricingReferenceFile.addEventListener("change", handlePricingReferenceFileChange);
  elements.pricingReferenceCurrency?.addEventListener("change", () => {
    syncPricingReferenceCurrencyCustomInput();
    if (state.pendingPricingReference) {
      state.pendingPricingReference.currency = pricingReferenceModalCurrency();
      state.pricingReferenceSavedNotice = "";
      renderPricingReferencePreview(state.pendingPricingReference);
    }
  });
  elements.pricingReferenceCurrencyCustom?.addEventListener("input", () => {
    const normalizedValue = normalizedCustomCurrencyInput();
    if (elements.pricingReferenceCurrencyCustom.value !== normalizedValue) {
      elements.pricingReferenceCurrencyCustom.value = normalizedValue;
    }
    if (state.pendingPricingReference) {
      state.pendingPricingReference.currency = pricingReferenceModalCurrency();
      state.pricingReferenceSavedNotice = "";
      renderPricingReferencePreview(state.pendingPricingReference);
      return;
    }
    setPricingReferenceSaveButtonState({
      canSave: !pricingReferenceSaveBlockReason(state.pendingPricingReference),
      reason: pricingReferenceSaveBlockReason(state.pendingPricingReference),
    });
  });
  elements.pricingReferenceTaxLabel?.addEventListener("change", () => {
    if (state.pendingPricingReference) {
      state.pendingPricingReference.tax = pricingReferenceModalTax();
      markPricingReferenceDraftChanged();
    }
  });
  elements.pricingReferenceTaxRate?.addEventListener("input", () => {
    if (state.pendingPricingReference) {
      state.pendingPricingReference.tax = pricingReferenceModalTax();
      markPricingReferenceDraftChanged();
    }
  });
  elements.pricingReferenceName?.addEventListener("input", () => {
    if (state.pendingPricingReference) {
      markPricingReferenceDraftChanged();
    }
  });
  elements.pricingReferencePreview?.addEventListener("input", (event) => {
    handlePricingReferencePreviewInput(event);
  });
  elements.pricingReferencePreview?.addEventListener("focusin", handlePricingReferencePreviewFocus);
  elements.pricingReferencePreview?.addEventListener("click", (event) => {
    if (event.target.closest("[data-pricing-reference-table-open]")) openPricingReferenceTableOverlay();
  });
  elements.pricingReferenceManageStatus?.addEventListener("click", (event) => {
    if (event.target.closest("[data-pricing-reference-table-open]")) openPricingReferenceTableOverlay();
  });
  elements.pricingReferenceTableBody?.addEventListener("focusin", handlePricingReferencePreviewFocus);
  elements.pricingReferenceTableBody?.addEventListener("input", handlePricingReferencePreviewInput);
  elements.pricingReferenceTableBody?.addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-pricing-reference-remove-row]");
    if (!removeButton) return;
    removePricingReferenceTableRow(Number(removeButton.dataset.pricingReferenceRemoveRow));
  });
  elements.pricingReferenceAddRowButton?.addEventListener("click", addPricingReferenceTableRow);
  elements.pricingReferenceUndoButton?.addEventListener("click", undoPricingReferenceTableEdit);
  elements.pricingReferenceTableCloseButton?.addEventListener("click", closePricingReferenceTableOverlay);
  elements.pricingReferenceTableOverlay?.addEventListener("click", (event) => {
    if (event.target === elements.pricingReferenceTableOverlay || event.target.closest("[data-pricing-reference-table-close]")) closePricingReferenceTableOverlay();
  });
  elements.pricingReferenceCancelButton.addEventListener("click", closePricingReferenceModal);
  elements.pricingReferenceCloseButton.addEventListener("click", closePricingReferenceModal);
  elements.pricingReferenceModal.addEventListener("click", blockPricingReferenceBusyInteraction, true);
  elements.pricingReferenceModal.addEventListener("click", (event) => {
    if (event.target === elements.pricingReferenceModal || event.target.closest("[data-pricing-reference-close]")) {
      closePricingReferenceModal();
    }
  });
  elements.analysisConfirmCancelButton.addEventListener("click", closeAnalysisConfirmModal);
  elements.analysisConfirmStartButton.addEventListener("click", () => confirmStartAnalysis("standard"));
  elements.analysisConfirmHighQualityButton?.addEventListener("click", () => confirmStartAnalysis("high_quality"));
  elements.analysisConfirmModal.addEventListener("click", (event) => {
    if (event.target.closest("[data-analysis-confirm-close]")) closeAnalysisConfirmModal();
  });
  elements.pricingMatchesBody.addEventListener("pointerdown", handleOutputIncludedPointerDown);
  elements.pricingMatchesBody.addEventListener("click", handleOutputCellClick);
  elements.pricingMatchesBody.addEventListener("dblclick", handleOutputCellOpen);
  elements.pricingMatchesBody.addEventListener("keydown", handleOutputCellKeydown);
  elements.pricingMatchesBody.addEventListener("focusout", handleOutputEditorCommit);
  elements.pricingMatchesBody.addEventListener("change", handleOutputEditorCommit);
  elements.resetImagesButton.addEventListener("click", resetImagesDraft);
  elements.analyseAgainButton.addEventListener("click", () => {
    state.pendingFeedback = "";
    requestStartAnalysis("standard");
  });
  elements.resetQuoteBasisButton.addEventListener("click", resetQuoteBasisToOriginal);
  elements.resetOutputButton.addEventListener("click", resetOutputDraft);
  elements.presetSelect.addEventListener("change", handlePresetSelectChange);
  elements.loadPresetButton?.addEventListener("click", loadCurrentPreset);
  elements.savePresetButton.addEventListener("click", saveCurrentPreset);
  elements.profileActionsMenuButton?.addEventListener("click", toggleProfileActionsMenu);
  elements.profileActionsShell?.addEventListener("keydown", handleProfileActionsMenuKeydown);
  document.addEventListener("click", handleProfileActionsDocumentClick);
  elements.deletePresetButton.addEventListener("click", (event) => {
    closeProfileActionsMenu();
    requestSelectedPresetDelete(event);
  });
  elements.cancelProfileNameButton?.addEventListener("click", hideProfileNameModal);
  elements.confirmProfileNameButton?.addEventListener("click", confirmProfileNameSave);
  elements.cancelProfileLoadButton?.addEventListener("click", () => hideProfileLoadModal({ focusButton: true }));
  elements.confirmProfileLoadButton?.addEventListener("click", confirmSelectedPresetLoad);
  elements.profileLoadModal?.addEventListener("click", (event) => {
    if (event.target.closest("[data-profile-load-close]")) {
      hideProfileLoadModal({ focusButton: true });
    }
  });
  elements.cancelProfileOverwriteButton?.addEventListener("click", () => hideProfileOverwriteModal({ focusInput: true }));
  elements.confirmProfileOverwriteButton?.addEventListener("click", confirmProfileOverwriteSave);
  elements.profileOverwriteModal?.addEventListener("click", (event) => {
    if (event.target.closest("[data-profile-overwrite-close]")) {
      hideProfileOverwriteModal({ focusInput: true });
    }
  });
  elements.profileNameInput?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    confirmProfileNameSave();
  });
  elements.profileNameModal?.addEventListener("click", (event) => {
    if (event.target.closest("[data-profile-name-close]")) {
      hideProfileNameModal();
    }
  });
  elements.cancelProfileDeleteButton?.addEventListener("click", hideProfileDeleteModal);
  elements.confirmProfileDeleteButton?.addEventListener("click", deleteSelectedPreset);
  elements.profileDeleteModal?.addEventListener("click", (event) => {
    if (event.target.closest("[data-profile-delete-close]")) {
      hideProfileDeleteModal();
    }
  });
  elements.cancelOutputDeleteButton?.addEventListener("click", hideOutputDeleteModal);
  elements.confirmOutputDeleteButton?.addEventListener("click", confirmOutputRowDelete);
  elements.outputDeleteModal?.addEventListener("click", (event) => {
    if (event.target.closest("[data-output-delete-close]")) {
      hideOutputDeleteModal();
    }
  });
  elements.cancelQuoteSessionDeleteButton?.addEventListener("click", hideQuoteSessionDeleteModal);
  elements.confirmQuoteSessionDeleteButton?.addEventListener("click", confirmQuoteSessionDelete);
  elements.quoteSessionDeleteModal?.addEventListener("click", (event) => {
    if (event.target.closest("[data-quote-session-delete-close]")) {
      hideQuoteSessionDeleteModal();
    }
  });
  elements.importPresetButton?.addEventListener("click", (event) => {
    closeProfileActionsMenu();
    requestPresetImport(event);
  });
  elements.importPresetFile?.addEventListener("change", handlePresetImportFileChange);
  elements.exportPresetButton?.addEventListener("click", (event) => {
    closeProfileActionsMenu();
    exportCurrentPreset(event);
  });
  elements.clearCustomerButton.addEventListener("click", clearCustomerDetails);
  elements.clearQuoteCompanyButton.addEventListener("click", clearQuoteCompanyDetails);
  [
    elements.clientName,
    elements.clientAttention,
    elements.clientTitle,
    elements.clientAddress,
    elements.projectTitle,
    elements.showName,
    elements.quoteDate,
    elements.projectNumber,
    elements.headerDetails,
    elements.termsHeading,
    elements.paymentTerms,
    elements.notesHeading,
    elements.standardNotes,
    elements.quoteCompanyName,
    elements.acceptanceText,
    elements.companySignatory,
    elements.companyTitle,
    elements.companyDateLabel,
    elements.personLabel,
    elements.stampLabel,
    elements.dateLabel,
    elements.taxLabel,
    elements.taxRate,
    elements.quoteCurrency,
    elements.quoteCurrencyCustom,
    elements.quoteTaxLabel,
    elements.quoteTaxRate,
    elements.quoteExchangeRate,
  ].filter(Boolean).forEach((input) => {
    input.addEventListener("input", handleQuoteDetailFieldChange);
    input.addEventListener("change", handleQuoteDetailFieldChange);
  });
  elements.basisChatForm.addEventListener("submit", handleBasisChatSubmit);
  elements.basisChatApplyButton.addEventListener("click", applyBasisChatProposal);
  elements.basisChatKeepButton.addEventListener("click", keepCurrentBasis);
  elements.basisChatCloseButton.addEventListener("click", closeBasisChatOverlay);
  elements.basisChatOverlay.addEventListener("click", (event) => {
    if (event.target.closest("[data-basis-chat-close]")) {
      closeBasisChatOverlay();
    }
  });
  elements.basisChatMessages.addEventListener("click", (event) => {
    const button = event.target.closest("[data-basis-chat-action]");
    if (!button) return;
    if (button.dataset.basisChatAction === "apply") applyBasisChatProposal();
    if (button.dataset.basisChatAction === "discard") keepCurrentBasis();
  });
  elements.basisChatPrompt.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    if (!elements.basisChatSendButton.disabled) {
      elements.basisChatForm.requestSubmit();
    }
  });
  elements.basisReviewSurface.addEventListener("click", handleQuoteBasisClick);
  elements.pricingMatchesBody.addEventListener("input", handleOutputRowEdit);
  elements.pricingMatchesBody.addEventListener("change", handleOutputRowEdit);
}

async function setInitialValues() {
  updateGeneratorCopy();
  syncAnalysisCreditLabels();
  renderProfileOptions();
  renderPresetOptions();
  renderHeaderLogoPreview();
  renderPresetStatus();
  if (await restoreSessionState()) {
    syncControlStates();
    return true;
  }
  applyDefaultQuoteDate();
  applyDefaultQuoteCompanyFields();
  loadDefaultProfilePreset({ silent: true });
  renderFiles();
  renderPricingMatches([]);
  renderMatchSummary({});
  setWorkflowStage("needs_images");
  renderBasisEmptyState();
  syncControlStates();
  setSidePanel("images");
  return false;
}

async function boot() {
  let dashboardOperation = null;
  let workspaceView = null;
  setDashboardLoadingState(true, { title: "Restoring workspace", message: "Loading your saved quote and returning to the last open menu." });
  wireEvents();
  clearLegacyLocalCompanyPresets();
  try {
    await initializeSession();
    workspaceView = restoredWorkspaceViewState();
    dashboardOperation = restoredDashboardOperation();
    if (dashboardOperation) setDashboardLoadingState(true, dashboardOperationLoadingOptions(dashboardOperation));
    await loadProfiles();
    const quoteRestored = await setInitialValues();
    if (workspaceView) applyWorkspaceViewState(workspaceView, { quoteRestored });
    if (state.activeAppView === "quote") showQuoteFlow();
    else showDashboard({ load: false });
    checkHealth();
  } finally {
    state.isBooting = false;
    syncControlStates();
  }
  if (state.activeJob) setDashboardLoadingState(false);
  await resumeSavedJob();
  if (dashboardOperation && !state.activeJob) {
    const resumed = await resumeDashboardOperation(dashboardOperation);
    if (resumed) {
      restoreRestorableOverlay();
      return;
    }
  }
  if (state.activeAppView === "quote") {
    showQuoteFlow();
    setDashboardLoadingState(false);
  } else {
    showDashboard({ load: false });
    await loadQuoteDashboard({ preserveViewState: true });
  }
  restoreRestorableOverlay();
}

boot();



