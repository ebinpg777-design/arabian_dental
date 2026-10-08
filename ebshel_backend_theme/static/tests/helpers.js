import { createWebClient } from "@web/../tests/webclient/helpers";
import { WebClientEnterprise } from "@ebshel_backend_theme/webclient/webclient";

export function createEnterpriseWebClient(params) {
    params.WebClientClass = WebClientEnterprise;
    return createWebClient(params);
}
