package com.codec.quantserver.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class CycleWatchUpdateRequest {
    private String note;
    @JsonProperty("planned_low_price")
    private Double plannedLowPrice;
    @JsonProperty("planned_high_price")
    private Double plannedHighPrice;
    private Boolean enabled;

    public String getNote() { return note; }
    public void setNote(String note) { this.note = note; }
    public Double getPlannedLowPrice() { return plannedLowPrice; }
    public void setPlannedLowPrice(Double plannedLowPrice) { this.plannedLowPrice = plannedLowPrice; }
    public Double getPlannedHighPrice() { return plannedHighPrice; }
    public void setPlannedHighPrice(Double plannedHighPrice) { this.plannedHighPrice = plannedHighPrice; }
    public Boolean getEnabled() { return enabled; }
    public void setEnabled(Boolean enabled) { this.enabled = enabled; }
}
