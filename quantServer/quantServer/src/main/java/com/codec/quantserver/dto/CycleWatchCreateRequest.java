package com.codec.quantserver.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class CycleWatchCreateRequest {
    @JsonProperty("ts_code")
    private String tsCode;
    private String note;
    @JsonProperty("planned_low_price")
    private Double plannedLowPrice;
    @JsonProperty("planned_high_price")
    private Double plannedHighPrice;

    public String getTsCode() { return tsCode; }
    public void setTsCode(String tsCode) { this.tsCode = tsCode; }
    public String getNote() { return note; }
    public void setNote(String note) { this.note = note; }
    public Double getPlannedLowPrice() { return plannedLowPrice; }
    public void setPlannedLowPrice(Double plannedLowPrice) { this.plannedLowPrice = plannedLowPrice; }
    public Double getPlannedHighPrice() { return plannedHighPrice; }
    public void setPlannedHighPrice(Double plannedHighPrice) { this.plannedHighPrice = plannedHighPrice; }
}
