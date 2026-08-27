<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0"
  xmlns="http://www.opengis.net/sld"
  xmlns:ogc="http://www.opengis.net/ogc"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.opengis.net/sld http://schemas.opengis.net/sld/1.0.0/StyledLayerDescriptor.xsd">
  <NamedLayer>
    <Name>hazard_class</Name>
    <UserStyle>
      <Title>Latest calculated flood hazard class</Title>
      <Abstract>Core Analyst native classification: 1 low, 2 medium, 3 high, 0 no data.</Abstract>
      <FeatureTypeStyle>
        <Rule>
          <RasterSymbolizer>
            <Opacity>0.72</Opacity>
            <ColorMap type="values">
              <ColorMapEntry color="#000000" quantity="0" opacity="0" label="No data"/>
              <ColorMapEntry color="#38bdf8" quantity="1" opacity="0.48" label="Low"/>
              <ColorMapEntry color="#f59e0b" quantity="2" opacity="0.74" label="Medium"/>
              <ColorMapEntry color="#dc2626" quantity="3" opacity="0.90" label="High"/>
            </ColorMap>
          </RasterSymbolizer>
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>
