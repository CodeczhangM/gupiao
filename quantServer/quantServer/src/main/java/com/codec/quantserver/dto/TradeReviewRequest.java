package com.codec.quantserver.dto;

public class TradeReviewRequest {

    private String tsCode;
    private String buyDate;
    private Double buyPrice;
    private String positionStatus = "holding";
    private String sellDate;
    private Double sellPrice;
    private String lossStatus;
    private String holdingNote;

    public String getTsCode() { return tsCode; }
    public void setTsCode(String tsCode) { this.tsCode = tsCode; }
    public String getBuyDate() { return buyDate; }
    public void setBuyDate(String buyDate) { this.buyDate = buyDate; }
    public Double getBuyPrice() { return buyPrice; }
    public void setBuyPrice(Double buyPrice) { this.buyPrice = buyPrice; }
    public String getPositionStatus() { return positionStatus; }
    public void setPositionStatus(String positionStatus) { this.positionStatus = positionStatus; }
    public String getSellDate() { return sellDate; }
    public void setSellDate(String sellDate) { this.sellDate = sellDate; }
    public Double getSellPrice() { return sellPrice; }
    public void setSellPrice(Double sellPrice) { this.sellPrice = sellPrice; }
    public String getLossStatus() { return lossStatus; }
    public void setLossStatus(String lossStatus) { this.lossStatus = lossStatus; }
    public String getHoldingNote() { return holdingNote; }
    public void setHoldingNote(String holdingNote) { this.holdingNote = holdingNote; }
}
