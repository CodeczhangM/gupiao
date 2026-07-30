package com.codec.quantserver.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.util.Map;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class FreeReviewQueryRequest {

    @JsonProperty("trade_date")
    private String tradeDate;

    @JsonProperty("score_version")
    private String scoreVersion;

    private String keyword;
    private List<String> industries;
    private List<String> areas;
    private List<String> markets;

    @JsonProperty("profit_state")
    private String profitState;

    @JsonProperty("volume_state")
    private String volumeState;

    @JsonProperty("growth_state")
    private String growthState;

    private Map<String, FreeReviewRange> ranges;

    @JsonProperty("sort_by")
    private String sortBy = "total_score";

    @JsonProperty("sort_direction")
    private String sortDirection = "desc";

    private Integer page = 1;

    @JsonProperty("page_size")
    private Integer pageSize = 50;

    @JsonProperty("visible_columns")
    private List<String> visibleColumns;

    public String getTradeDate() {
        return tradeDate;
    }

    public void setTradeDate(String tradeDate) {
        this.tradeDate = tradeDate;
    }

    public String getScoreVersion() {
        return scoreVersion;
    }

    public void setScoreVersion(String scoreVersion) {
        this.scoreVersion = scoreVersion;
    }

    public String getKeyword() {
        return keyword;
    }

    public void setKeyword(String keyword) {
        this.keyword = keyword;
    }

    public List<String> getIndustries() {
        return industries;
    }

    public void setIndustries(List<String> industries) {
        this.industries = industries;
    }

    public List<String> getAreas() {
        return areas;
    }

    public void setAreas(List<String> areas) {
        this.areas = areas;
    }

    public List<String> getMarkets() {
        return markets;
    }

    public void setMarkets(List<String> markets) {
        this.markets = markets;
    }

    public String getProfitState() {
        return profitState;
    }

    public void setProfitState(String profitState) {
        this.profitState = profitState;
    }

    public String getVolumeState() {
        return volumeState;
    }

    public void setVolumeState(String volumeState) {
        this.volumeState = volumeState;
    }

    public String getGrowthState() {
        return growthState;
    }

    public void setGrowthState(String growthState) {
        this.growthState = growthState;
    }

    public Map<String, FreeReviewRange> getRanges() {
        return ranges;
    }

    public void setRanges(Map<String, FreeReviewRange> ranges) {
        this.ranges = ranges;
    }

    public String getSortBy() {
        return sortBy;
    }

    public void setSortBy(String sortBy) {
        this.sortBy = sortBy;
    }

    public String getSortDirection() {
        return sortDirection;
    }

    public void setSortDirection(String sortDirection) {
        this.sortDirection = sortDirection;
    }

    public Integer getPage() {
        return page;
    }

    public void setPage(Integer page) {
        this.page = page;
    }

    public Integer getPageSize() {
        return pageSize;
    }

    public void setPageSize(Integer pageSize) {
        this.pageSize = pageSize;
    }

    public List<String> getVisibleColumns() {
        return visibleColumns;
    }

    public void setVisibleColumns(List<String> visibleColumns) {
        this.visibleColumns = visibleColumns;
    }
}
